"""
Predictive Dependency Prefetcher: Analyzes code imports in real time
and attaches minimal signatures of imported symbols to file read responses.
Eliminates downstream tool calls (read_file) and speeds up agent resolution.
"""

import os
import re
import ast
from typing import List, Dict, Tuple, Optional, Any
from src.tokenizer import count_tokens

class DependencyPrefetcher:
    """Extracts and formats signatures of local relative imported dependencies."""

    @classmethod
    def prefetch_for_file(cls, file_path: str, max_deps: int = 3) -> Tuple[str, int]:
        """
        Parses imports in file_path, locates local files, and extracts imported symbol signatures.
        Returns: (prefetched_block_text, est_tokens_saved)
        """
        if not file_path or not os.path.exists(file_path):
            return "", 0

        ext = os.path.splitext(file_path)[1].lower()
        file_dir = os.path.dirname(os.path.abspath(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return "", 0

        # Parse local relative imports
        imported_symbols: List[Dict[str, Any]] = []

        if ext == ".py":
            imported_symbols = cls._parse_python_imports(content, file_dir)
        elif ext in (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs"):
            imported_symbols = cls._parse_ts_imports(content, file_dir)

        if not imported_symbols:
            return "", 0

        prefetched_lines = []
        is_python = (ext == ".py")
        comment_prefix = "#" if is_python else "//"

        prefetched_lines.append(f"{comment_prefix} [TOCP PREFETCHED DEPENDENCIES: Signatures attached to prevent extra read_file roundtrips]")

        count = 0
        tokens_saved_estimate = 0

        for item in imported_symbols[:max_deps]:
            dep_path = item["path"]
            symbols = item["symbols"]
            signatures = cls._extract_signatures_from_dep(dep_path, symbols, is_python)
            if signatures:
                count += 1
                base_dep = os.path.basename(dep_path)
                prefetched_lines.append(f"{comment_prefix} • {base_dep} -> {signatures}")
                # Each eliminated read_file call saves ~1,200 tokens
                tokens_saved_estimate += 1200

        if count == 0:
            return "", 0

        prefetched_lines.append(f"{comment_prefix} [End Prefetched Signatures]\n")
        return "\n".join(prefetched_lines) + "\n", tokens_saved_estimate

    @classmethod
    def _parse_python_imports(cls, code: str, current_dir: str) -> List[Dict[str, Any]]:
        """Extracts relative/local imports in Python."""
        results = []
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    level = node.level  # level > 0 means relative e.g. from .service import Foo
                    symbols = [alias.name for alias in node.names]

                    dep_path = None
                    mod_rel = os.path.join(*module.split(".")) if module else ""
                    if level > 0:
                        # Relative import: level=1 -> '.', level=2 -> '..', level=3 -> '../..'
                        parents = [".."] * (level - 1)
                        target_dir = os.path.abspath(os.path.join(current_dir, *parents)) if parents else current_dir
                        if mod_rel:
                            for cand in (
                                os.path.join(target_dir, f"{mod_rel}.py"),
                                os.path.join(target_dir, mod_rel, "__init__.py"),
                            ):
                                if os.path.exists(cand):
                                    dep_path = cand
                                    break
                        else:
                            cand = os.path.join(target_dir, "__init__.py")
                            if os.path.exists(cand):
                                dep_path = cand
                    elif mod_rel:
                        for cand in (
                            os.path.join(current_dir, f"{mod_rel}.py"),
                            os.path.join(current_dir, mod_rel, "__init__.py"),
                        ):
                            if os.path.exists(cand):
                                dep_path = cand
                                break

                    if dep_path and os.path.exists(dep_path):
                        results.append({"path": dep_path, "symbols": symbols})
        except Exception:
            pass

        return results

    @classmethod
    def _parse_ts_imports(cls, code: str, current_dir: str) -> List[Dict[str, Any]]:
        """Extracts relative imports in TypeScript/JavaScript."""
        results = []
        # Matches: import { A, B } from './path' or import A from '../path'
        import_regex = re.compile(
            r"""import\s+(?:(?:\*\s+as\s+[a-zA-Z0-9_]+)|(?:\{([^}]+)\})|([a-zA-Z0-9_]+))\s+from\s+['"](\.[^'"]+)['"]""",
            re.MULTILINE
        )

        for match in import_regex.finditer(code):
            named_imports = match.group(1)
            default_import = match.group(2)
            rel_path = match.group(3)

            symbols = []
            if named_imports:
                symbols = [s.strip().split(" as ")[0].strip() for s in named_imports.split(",") if s.strip()]
            elif default_import:
                symbols = [default_import.strip()]

            # Resolve target file path (handles already-extensioned ESM imports like './foo.js' -> './foo.ts')
            base_target = os.path.normpath(os.path.join(current_dir, rel_path))
            base_no_js = re.sub(r"\.(?:mjs|cjs|jsx|js)$", "", base_target)
            candidates: List[str] = []
            if os.path.isfile(base_target):
                candidates.append(base_target)
            for root_cand in dict.fromkeys((base_target, base_no_js)):
                for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js", "/index.mjs"):
                    candidates.append(f"{root_cand}{ext}")
            resolved = next((c for c in candidates if os.path.isfile(c)), None)

            if resolved and symbols:
                results.append({"path": resolved, "symbols": symbols})

        return results

    @classmethod
    def _extract_signatures_from_dep(cls, dep_path: str, symbols: List[str], is_python: bool) -> str:
        """Extracts concise signature for specific symbols from target dependency file."""
        try:
            with open(dep_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception:
            return ""

        extracted = []

        if is_python:
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name in symbols:
                            if isinstance(node, ast.ClassDef):
                                # Extract class with method names
                                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                                meth_str = ", ".join(methods[:4]) if methods else "..."
                                extracted.append(f"class {node.name}(methods: {meth_str})")
                            else:
                                # Function signature
                                args_list = [a.arg for a in node.args.args]
                                extracted.append(f"def {node.name}({', '.join(args_list)})")
            except Exception:
                pass
        else:
            # TS/JS interface/class/type/function extraction (brace-depth safe)
            for sym in symbols:
                decl_re = re.compile(rf"(?:export\s+(?:default\s+)?)?(?:declare\s+)?(interface|type|class|enum)\s+{re.escape(sym)}\b[^{{;]*")
                dm = decl_re.search(code)
                if dm:
                    kind = dm.group(1)
                    rest = code[dm.end():]
                    if rest.lstrip().startswith("{"):
                        brace_idx = code.find("{", dm.end())
                        depth = 0
                        end_idx = brace_idx
                        for idx in range(brace_idx, min(len(code), brace_idx + 2000)):
                            ch = code[idx]
                            if ch == "{":
                                depth += 1
                            elif ch == "}":
                                depth -= 1
                                if depth == 0:
                                    end_idx = idx
                                    break
                        body_clean = re.sub(r"\s+", " ", code[brace_idx + 1 : end_idx].strip())
                        if len(body_clean) > 120:
                            body_clean = body_clean[:117] + "..."
                        extracted.append(f"{kind} {sym} {{ {body_clean} }}")
                        continue
                fn_pattern = re.compile(rf"""(?:export\s+(?:default\s+)?)?(?:async\s+)?(function|const|let)\s+{re.escape(sym)}\b[^\n;]*""", re.MULTILINE)
                fm = fn_pattern.search(code)
                if fm:
                    sig = fm.group(0).replace("export default ", "").replace("export ", "").strip()
                    sig = sig.rstrip("{").strip()
                    sig = re.sub(r"\s+", " ", sig)
                    extracted.append(sig)
                else:
                    extracted.append(sym)

        return "; ".join(extracted) if extracted else ", ".join(symbols)
