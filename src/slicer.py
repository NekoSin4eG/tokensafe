"""
Tool Call Optimizer: Intercepts and slices excessive tool inputs and outputs.
Prevents context window bloat from full-file reads (800+ lines) and noisy terminal logs.
"""

import os
import re
from typing import Dict, Any, Optional, Tuple
from src.tokenizer import count_tokens

class ToolSlicer:
    """Optimizes tool execution parameters to minimize token consumption."""

    # Default max lines to read when no range is specified on large files
    DEFAULT_MAX_WINDOW = 350

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
            for candidate in candidates:
                sym_regex = re.compile(r'\b(def|function|class|const|let|var|type|interface)\s+' + re.escape(candidate) + r'\b')
                for idx, line in enumerate(lines, 1):
                    if sym_regex.search(line):
                        found_line = idx
                        target_symbol = candidate
                        break
                if found_line:
                    break

        new_start = 1
        new_end = total_lines

        if found_line:
            # Slice: 20 lines before, 80 lines after
            new_start = max(1, found_line - 20)
            new_end = min(total_lines, found_line + 80)

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
