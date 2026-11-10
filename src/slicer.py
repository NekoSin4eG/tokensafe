"""
Tool Call Optimizer: Intercepts and slices excessive tool inputs and outputs.
Prevents context window bloat from full-file reads (800+ lines) and noisy terminal logs.
"""

import os
import re
from typing import Dict, Any, List, Optional, Tuple
from src.tokenizer import count_tokens

class ToolSlicer:
    """Optimizes tool execution parameters to minimize token consumption."""

    # Default max lines to read when no range is specified on large files
    DEFAULT_MAX_WINDOW = 350

    # When a target symbol is found, the slice must contain the WHOLE
    # enclosing definition — a fixed tail used to cut long function bodies in
    # half. Definitions longer than this cap are better served by the AST
    # skeleton path, so the window stays bounded.
    MAX_SYMBOL_WINDOW = 400

    @classmethod
    def _find_definition_end(cls, lines: List[str], def_line_no: int) -> Optional[int]:
        """
        Returns the 1-based last line of the definition whose declaration sits
        on def_line_no, or None when it cannot be determined reliably.

        Python (def/class): the body ends at the last non-blank line before a
        line dedented to <= the declaration's indent.
        C-family (function/const/func/fn/impl/struct/...): brace balance from
        the first '{' within the declaration or the next 10 lines. Naive
        counting is safe here: a brace inside a string can only skew the end
        within the cap, and the caller keeps the legacy tail as a floor.
        """
        if def_line_no < 1 or def_line_no > len(lines):
            return None
        def_line = lines[def_line_no - 1]

        py_match = re.match(r"^(\s*)(?:async\s+)?(?:def|class)\b", def_line)
        if py_match:
            base_indent = len(py_match.group(1).expandtabs(8))
            end = def_line_no
            for idx in range(def_line_no, len(lines)):
                line = lines[idx]
                if not line.strip():
                    continue
                expanded = line.expandtabs(8)
                indent = len(expanded) - len(expanded.lstrip())
                if indent <= base_indent:
                    break
                end = idx + 1
            return end

        # C-family: locate the opening brace within the declaration head
        # (first 10 lines), then balance braces across the capped window.
        window_end = min(len(lines), def_line_no - 1 + cls.MAX_SYMBOL_WINDOW)
        text = "".join(lines[def_line_no - 1:window_end])
        head = "".join(lines[def_line_no - 1:min(len(lines), def_line_no + 10)])
        offset = head.find("{")
        if offset == -1:
            return None

        depth = 0
        for i, ch in enumerate(text[offset:]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return def_line_no + text[:offset + i + 1].count("\n")
        # Unbalanced within the cap: report the cap boundary as the end
        return window_end

    @classmethod
    def optimize_read_file(cls, args: Dict[str, Any], context_hint: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Optimizes `read_file` tool call arguments.
        If a file is large and no bounds are given, locates target symbol or applies windowing.
        Returns: (modified_args_or_none, metrics_dict)
        """
        path = args.get("Path", "")
        start_line = args.get("StartLine")
        end_line = args.get("EndLine")

        if not path or not os.path.isabs(path) or not os.path.exists(path):
            return {}, {"saved_tokens": 0, "original_tokens": 0, "optimized_tokens": 0}

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return {}, {"saved_tokens": 0, "original_tokens": 0, "optimized_tokens": 0}

        total_lines = len(lines)
        raw_content = "".join(lines)
        total_tokens = count_tokens(raw_content)

        # If file is small (< 150 lines) or specific range already requested by model, don't intervene
        if total_lines <= 150 or (start_line is not None and end_line is not None):
            return {}, {
                "saved_tokens": 0,
                "original_tokens": total_tokens,
                "optimized_tokens": total_tokens
            }

        # Check if a target symbol is mentioned in context_hint or user query
        found_line = None
        target_symbol = ""
        if context_hint:
            words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b', context_hint)
            ignore_set = {"function", "method", "class", "async", "const", "return", "optimize", "refactor", "debug", "test", "check", "please", "code", "file"}
            candidates = [w for w in words if w.lower() not in ignore_set]
            if candidates:
                # Single pass over the file for ALL candidates (previously O(words x lines)
                # regex scans per read_file hook call on large files).
                unique_candidates = list(dict.fromkeys(candidates))
                combined = re.compile(
                    r'\b(?:def|function|class|const|let|var|type|interface|func|fn|impl|struct|enum|trait)\s+('
                    + "|".join(re.escape(c) for c in unique_candidates) + r')\b'
                )
                first_hit: Dict[str, int] = {}
                for idx, line in enumerate(lines, 1):
                    m = combined.search(line)
                    if m and m.group(1) not in first_hit:
                        first_hit[m.group(1)] = idx
                # Preserve original semantics: highest-priority candidate that has a match
                for candidate in candidates:
                    if candidate in first_hit:
                        target_symbol = candidate
                        found_line = first_hit[candidate]
                        break

        new_start = 1
        new_end = total_lines

        if found_line:
            # Slice: 20-line lead-in before the declaration, then enough tail
            # to contain the WHOLE enclosing definition (short definitions keep
            # the legacy 80-line tail; long ones extend to their real end).
            new_start = max(1, found_line - 20)
            new_end = min(total_lines, found_line + 80)
            def_end = cls._find_definition_end(lines, found_line)
            if def_end:
                new_end = min(
                    total_lines,
                    max(new_end, min(def_end, found_line + cls.MAX_SYMBOL_WINDOW)),
                )

        # If no symbol found and file is large (> 400 lines), apply safety window
        if not target_symbol and total_lines > 400 and (end_line is None or end_line > cls.DEFAULT_MAX_WINDOW):
            new_start = start_line if start_line is not None else 1
            new_end = min(total_lines, new_start + cls.DEFAULT_MAX_WINDOW)

        # Calculate tokens of sliced content
        sliced_content = "".join(lines[new_start - 1 : new_end])
        sliced_tokens = count_tokens(sliced_content)

        if sliced_tokens < total_tokens:
            saved = total_tokens - sliced_tokens
            overwrite = {
                "StartLine": new_start,
                "EndLine": new_end
            }
            metrics = {
                "saved_tokens": saved,
                "original_tokens": total_tokens,
                "optimized_tokens": sliced_tokens,
                "lines_total": total_lines,
                "lines_sliced": (new_end - new_start + 1),
                "target_symbol": target_symbol
            }
            return overwrite, metrics

        return {}, {"saved_tokens": 0, "original_tokens": total_tokens, "optimized_tokens": total_tokens}

    @classmethod
    def sanitize_command_log(cls, raw_output: str) -> Tuple[str, int, int]:
        """
        Sanitizes test/build command outputs by removing redundant node_modules stacks,
        excessive empty lines, and long progress bars.
        Returns: (sanitized_output, original_tokens, sanitized_tokens)
        """
        if not raw_output:
            return "", 0, 0

        orig_tokens = count_tokens(raw_output)
        lines = raw_output.splitlines()

        cleaned_lines = []
        skip_stack = False

        for line in lines:
            # Drop noisy node_modules / site-packages internal stack lines
            if re.search(r"at .*node_modules/|at .*site-packages/|at async Promise\.all", line):
                if not skip_stack:
                    cleaned_lines.append("    [... internal dependency stack omitted ...]")
                    skip_stack = True
                continue
            else:
                skip_stack = False

            # Drop progress bars
            if re.search(r"(\[=+>*\s*\]|\d+%\s*\|\s*█+)", line):
                continue

            cleaned_lines.append(line)

        sanitized = "\n".join(cleaned_lines)
        # Collapse multiple blank lines
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        sanitized_tokens = count_tokens(sanitized)

        return sanitized, orig_tokens, sanitized_tokens
