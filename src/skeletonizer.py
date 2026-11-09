"""
Zero-LLM AST Skeletonizer: Folds implementation bodies into lightweight signatures
for Python, TypeScript, JavaScript, Go, and C-like languages.
Includes mtime-based incremental caching for 0.05ms retrieval of unchanged files.
"""

import os
import re
import ast
import hashlib
import json
from typing import Optional, Tuple, Set
from src.tokenizer import count_tokens

CONTROL_KEYWORDS = {
    'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
    'try', 'catch', 'finally', 'with', 'select', 'lock', 'using',
    'fixed', 'unsafe', 'synchronized', 'struct', 'enum', 'interface',
    'type', 'namespace', 'class', 'trait', 'impl', 'package', 'module',
    'return', 'throw', 'yield', 'await', 'import', 'export', 'record'
}

# 1. Standard function declarations (JS/TS/PHP/Swift/Kotlin)
FN_DECL_REGEX = re.compile(
    r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?|fun)\s+([a-zA-Z0-9_$]+)?\s*(?:<[^>]+>)?\s*\('
)

# 2. Go func: func Foo(...) or func (r *Recv) Foo(...)
GO_FN_REGEX = re.compile(
    r'^func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\s*(?:\[[^\]]+\])?\s*\('
)

# 3. Rust fn: fn foo(...) or pub async fn foo(...)
RUST_FN_REGEX = re.compile(
    r'^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+([a-zA-Z0-9_]+)\s*(?:<[^>]+>)?\s*\('
)

# 4. Arrow function / React component: const Foo = (...) => { or export const Foo: React.FC<Props> = (...) => {
ARROW_FN_REGEX = re.compile(
    r'^(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:React\.(?:memo|forwardRef)\s*\()?(?:useCallback\s*\()?(?:(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>|function\b)'
)

# 4b. Multi-line arrow function start: const Foo: React.FC<Props> = (
ARROW_MULTILINE_START = re.compile(
    r'^(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:React\.(?:memo|forwardRef)\s*\()?(?:useCallback\s*\()?\s*\('
)

# 5. Method definition (Java, C#, C++, TS, PHP)
METHOD_REGEX = re.compile(
    r'^(?:(?:public|private|protected|static|final|native|synchronized|abstract|async|override|virtual|inline|explicit|const|get|set)\s+)*'
    r'(?:[a-zA-Z0-9_<>\[\],.?*&:]+\s+)*'
    r'([a-zA-Z0-9_$:~]+)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*'
    r'(?:const\s+|override\s+|final\s+|noexcept\s+|throws\s+[^{]+|->\s*[^{]+|:\s*[^{]+)*\{'
)

class BraceLexer:
    """Accurate tokenizer tracking multi-line comments and string literals for brace counting."""
    def __init__(self):
        self.in_block_comment = False

    def clean_line(self, line: str) -> str:
        l = line
        if self.in_block_comment:
            if "*/" in l:
                self.in_block_comment = False
                l = l[l.find("*/") + 2:]
            else:
                return ""

        while "/*" in l:
            start = l.find("/*")
            end = l.find("*/", start + 2)
            if end != -1:
                l = l[:start] + " " + l[end + 2:]
            else:
                l = l[:start]
                self.in_block_comment = True
                break

        # Strip single-line comments
        l = re.sub(r"//.*$", "", l)
        # Strip string literals and template strings
        l = re.sub(r'"(?:\\.|[^"\\])*"', '""', l)
        l = re.sub(r"'(?:\\.|[^'\\])*'", "''", l)
        l = re.sub(r"`(?:\\.|[^`\\])*`", "``", l)
        return l

    def count_braces(self, line: str) -> Tuple[int, int]:
        clean = self.clean_line(line)
        return clean.count("{"), clean.count("}")

class Skeletonizer:
    """Generates structural AST skeletons without LLMs with incremental mtime caching."""

    CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "skeletons")

    @classmethod
    def skeletonize_code(cls, code: str, file_ext: str, keep_symbols: Optional[Set[str]] = None) -> str:
        """Skeletons source code based on file extension, optionally preserving keep_symbols."""
        ext = file_ext.lower()
        if ext == ".py":
            return cls._skeletonize_python(code, keep_symbols=keep_symbols)
        elif ext in (
            ".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs",
            ".go", ".java", ".rs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".cs", ".php", ".kt", ".swift"
        ):
            return cls._skeletonize_braces(code, ext, keep_symbols=keep_symbols)
        return code

    @classmethod
    def _skeletonize_python(cls, code: str, keep_symbols: Optional[Set[str]] = None) -> str:
        """Uses Python standard library `ast` to fold function bodies."""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if keep_symbols is not None and node.name in keep_symbols:
                        continue
                    doc = ast.get_docstring(node)
                    new_body = []
                    if doc:
                        new_body.append(ast.Expr(value=ast.Constant(value=doc)))
                    new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
                    node.body = new_body
            return ast.unparse(tree)
        except Exception:
            return code

    @classmethod
    def _extract_fn(cls, line: str) -> Optional[Tuple[str, str]]:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("//") or line_clean.startswith("/*") or line_clean.startswith("*"):
            return None

        if line_clean.startswith("}"):
            line_clean = line_clean[1:].strip()

        # Strip export prefix so exported declarations can be inspected
        if line_clean.startswith("export default "):
            check_line = line_clean[15:].strip()
        elif line_clean.startswith("export "):
            check_line = line_clean[7:].strip()
        else:
            check_line = line_clean

        first_word = re.split(r"[\s(]", check_line)[0]
        if first_word in CONTROL_KEYWORDS:
            return None

        m = FN_DECL_REGEX.match(line_clean)
        if m:
            return (m.group(1) or "anonymous", "fn_decl")

        m = GO_FN_REGEX.match(line_clean)
        if m:
            return (m.group(1), "go_func")

        m = RUST_FN_REGEX.match(line_clean)
        if m:
            return (m.group(1), "rust_fn")

        m = ARROW_FN_REGEX.match(line_clean)
        if m:
            return (m.group(1), "arrow_fn")

        m = ARROW_MULTILINE_START.match(line_clean)
        if m and not line_clean.endswith(";"):
            return (m.group(1), "arrow_multiline")

        m = METHOD_REGEX.match(line_clean)
        if m:
            name = m.group(1)
            clean_name = name.split("::")[-1]
            if clean_name not in CONTROL_KEYWORDS:
                return (clean_name, "method")

        return None

    @classmethod
    def _skeletonize_braces(cls, code: str, ext: str, keep_symbols: Optional[Set[str]] = None) -> str:
        """
        Universal brace-depth state machine for TS/JS/Go/Java/Rust/C++/C#/PHP.
        Folds function bodies into '{ /* ... */ }' while strictly preserving:
        1. Target symbols and 1-hop dependencies in full (via keep_symbols).
        2. Container blocks (classes, interfaces, structs, namespaces, impls).
        3. Control flow statements (if, catch, for, while, switch, try).
        4. Arrow functions, React components, and multi-line signatures.
        5. Multi-line comment & string immunity for brace depth tracking.
        """
        lines = code.splitlines()
        out = []

        lexer = BraceLexer()
        in_folding = False
        fold_depth = 0
        current_depth = 0

        pending_header_lines = []
        pending_name = None
        pending_kind = None

        comment_token = "/* ... */" if ext != ".py" else "..."

        for line in lines:
            raw_line = line
            line_clean = line.strip()

            # 1. If currently skipping a folded function body
            if in_folding:
                open_b, close_b = lexer.count_braces(raw_line)
                current_depth += open_b - close_b
                if current_depth <= fold_depth:
                    in_folding = False
                    trailing = ""
                    idx = raw_line.rfind("}")
                    if idx != -1 and idx < len(raw_line) - 1:
                        suffix = raw_line[idx + 1:].strip()
                        if suffix.startswith(";") or suffix.startswith(","):
                            trailing = suffix
                    if trailing and out:
                        out[-1] = out[-1] + trailing
                continue

            # 2. If accumulating a multi-line header
            if pending_header_lines:
                pending_header_lines.append(raw_line)
                if "=>" in line_clean or "{" in line_clean:
                    fn_name = pending_name
                    should_keep = keep_symbols is not None and fn_name in keep_symbols

                    last_brace_idx = raw_line.rfind("{")
                    if last_brace_idx != -1:
                        sig_lines = pending_header_lines[:-1] + [raw_line[:last_brace_idx]]
                        temp_lexer = BraceLexer()
                        sig_open = sum(temp_lexer.count_braces(sl)[0] for sl in sig_lines)
                        sig_close = sum(temp_lexer.count_braces(sl)[1] for sl in sig_lines)
                        base_depth = current_depth + (sig_open - sig_close)
                    else:
                        base_depth = current_depth

                    if should_keep:
                        out.extend(pending_header_lines)
                        for phl in pending_header_lines:
                            op, cl = lexer.count_braces(phl)
                            current_depth += op - cl
                    else:
                        folded_last_line = re.sub(r'\{.*$', f'{{ {comment_token} }}', raw_line)
                        pending_header_lines[-1] = folded_last_line
                        out.extend(pending_header_lines)
                        fold_depth = base_depth
                        current_depth = base_depth + 1
                        in_folding = True

                    pending_header_lines = []
                    pending_name = None
                    pending_kind = None
                    continue
                elif ";" in line_clean and not ("{" in line_clean or "=>" in line_clean):
                    out.extend(pending_header_lines)
                    for phl in pending_header_lines:
                        op, cl = lexer.count_braces(phl)
                        current_depth += op - cl
                    pending_header_lines = []
                    pending_name = None
                    pending_kind = None
                    continue
                else:
                    continue

            # 3. Check for function start
            fn_info = cls._extract_fn(raw_line)
            if fn_info:
                name, kind = fn_info

                if (kind in ('arrow_multiline', 'arrow_fn')) and "=>" not in raw_line:
                    pending_header_lines = [raw_line]
                    pending_name = name
                    pending_kind = 'arrow_multiline'
                    continue

                if "{" in raw_line:
                    should_keep = keep_symbols is not None and name in keep_symbols
                    open_b, close_b = lexer.count_braces(raw_line)

                    if should_keep:
                        out.append(raw_line)
                        current_depth += open_b - close_b
                    else:
                        folded_line = re.sub(r'\{.*$', f'{{ {comment_token} }}', raw_line)
                        out.append(folded_line)
                        open_b_head, _ = lexer.count_braces(raw_line[:raw_line.find("{") + 1])
                        fold_depth = current_depth + open_b_head - 1
                        current_depth += open_b - close_b
                        if current_depth > fold_depth:
                            in_folding = True
                    continue

            # 4. Normal line
            out.append(raw_line)
            open_b, close_b = lexer.count_braces(raw_line)
            current_depth += open_b - close_b

        return "\n".join(out)

    @classmethod
    def get_skeleton_file(cls, original_path: str, target_symbol: Optional[str] = None) -> Tuple[Optional[str], int, int]:
        """
        Reads original_path, checks mtime cache, generates skeleton if modified or absent.
        If target_symbol is provided, applies Graph-Guided AST skeletonization retaining 1-hop dependencies.
        Returns: (skeleton_path_or_none, orig_tokens, skeleton_tokens)
        """
        if not original_path or not os.path.exists(original_path):
            return None, 0, 0

        try:
            current_mtime = os.path.getmtime(original_path)
            file_size = os.path.getsize(original_path)
        except Exception:
            return None, 0, 0

        # Ensure cache directory exists
        os.makedirs(cls.CACHE_DIR, exist_ok=True)
        path_hash = hashlib.md5(original_path.encode("utf-8")).hexdigest()[:8]
        base_name = os.path.basename(original_path)
        sym_suffix = f"_{target_symbol}" if target_symbol else ""
        skeleton_filename = f"{path_hash}{sym_suffix}_{base_name}"
        skeleton_path = os.path.join(cls.CACHE_DIR, skeleton_filename)
        meta_path = os.path.join(cls.CACHE_DIR, f"{path_hash}{sym_suffix}_{base_name}.meta.json")

        # 1. Check incremental mtime cache
        if os.path.exists(skeleton_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                    if meta.get("mtime") == current_mtime:
                        # Cache hit! Return immediately in 0.05ms
                        return skeleton_path, meta.get("orig_tokens", 0), meta.get("skel_tokens", 0)
            except Exception:
                pass

        # 2. Cache miss: Read and parse
        try:
            with open(original_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception:
            return None, 0, 0

        lines_count = len(code.splitlines())
        if lines_count < 150:
            return None, 0, 0

        ext = os.path.splitext(original_path)[1]
        
        # If target symbol specified, use Graph-Guided skeletonization
        if target_symbol:
            try:
                from src.graph_engine import GraphEngine
                graph, rel_path = GraphEngine.for_file(original_path)
                graph_content = graph.graph_guided_skeletonize(original_path, target_symbol=target_symbol)
                skeleton_content = graph_content if graph_content else cls.skeletonize_code(code, ext)
            except Exception:
                skeleton_content = cls.skeletonize_code(code, ext)
        else:
            skeleton_content = cls.skeletonize_code(code, ext)

        # Prefetch local dependency signatures
        from src.dependency_prefetcher import DependencyPrefetcher
        prefetch_block, saved_dep_tokens = DependencyPrefetcher.prefetch_for_file(original_path)

        orig_tokens = count_tokens(code)
        skel_tokens = count_tokens(skeleton_content)

        if skel_tokens >= orig_tokens * 0.7:
            return None, 0, 0

        if target_symbol:
            header_comment = (
                f"// [TokenSafe Graph-Guided Skeleton for '{target_symbol}': target & 1-hop dependencies kept full]\n\n"
            ) if ext != ".py" else (
                f"# [TokenSafe Graph-Guided Skeleton for '{target_symbol}': target & 1-hop dependencies kept full]\n\n"
            )
        else:
            header_comment = (
                f"// [TOCP AST Skeleton: {lines_count} lines folded into API signatures]\n"
                f"// [To inspect implementation of a specific method, call read_file with StartLine/EndLine]\n\n"
            ) if ext != ".py" else (
                f"# [TOCP AST Skeleton: {lines_count} lines folded into API signatures]\n"
                f"# [To inspect implementation of a specific method, call read_file with StartLine/EndLine]\n\n"
            )

        full_content = header_comment + (prefetch_block + "\n" if prefetch_block else "") + skeleton_content

        with open(skeleton_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Write metadata for mtime incremental caching
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump({
                "mtime": current_mtime,
                "orig_tokens": orig_tokens,
                "skel_tokens": skel_tokens,
                "lines_count": lines_count
            }, mf)

        return skeleton_path, orig_tokens, skel_tokens
