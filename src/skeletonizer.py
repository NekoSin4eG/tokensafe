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
from src.paths import resolve_data_dir

CONTROL_KEYWORDS = {
    'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
    'try', 'catch', 'finally', 'with', 'select', 'lock', 'using',
    'fixed', 'unsafe', 'synchronized', 'struct', 'enum', 'interface',
    'type', 'namespace', 'class', 'trait', 'impl', 'package', 'module',
    'return', 'throw', 'yield', 'await', 'import', 'export', 'record'
}

# 1. Standard function declarations (JS/TS/PHP/Kotlin)
FN_DECL_REGEX = re.compile(
    r'^(?:export\s+)?(?:default\s+)?(?:(?:public|private|protected|internal|open|override|suspend|final|abstract|static|inline|tailrec|operator|infix|external)\s+)*(?:async\s+)?(?:function\s*\*?|fun)\s+([a-zA-Z0-9_$]+)?\s*(?:<[^>]+>)?\s*\('
)

# 2. Go / Swift func: func Foo(...) or func (r *Recv) Foo(...) or public mutating func Foo(...)
GO_FN_REGEX = re.compile(
    r'^(?:(?:@[a-zA-Z0-9_]+(?:\([^)]*\))?\s+)*(?:public|private|fileprivate|internal|open|override|static|class|mutating|nonmutating|final)\s+)*func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\s*(?:\[[^\]]+\]|<[^>]+>)?\s*\('
)

# 3. Rust fn: fn foo(...) or pub async fn foo(...)
RUST_FN_REGEX = re.compile(
    r'^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+([a-zA-Z0-9_]+)\s*(?:<[^>]+>)?\s*\('
)

# 4. Arrow function / React component: const Foo = (...) => { or export const Foo: React.FC<Props> = (...) => {
# Supports single-line return types, generics, React.memo, useCallback
ARROW_FN_REGEX = re.compile(
    r'^(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)'
    r'(?:\s*:\s*[^=]+)?\s*=\s*'
    r'(?:async\s*)?'
    r'(?:React\.(?:memo|forwardRef)\s*\()?'
    r'(?:useCallback\s*\()?'
    r'(?:'
      r'(?:<[^>]+>\s*)?'
      r'(?:\([^)]*\)|[a-zA-Z0-9_$]+)'
      r'(?:\s*:\s*[^=>]+)?'
      r'\s*=>'
      r'|function\b'
    r')'
)

# 4b. Multi-line arrow function start: const Foo: React.FC<Props> = (
ARROW_MULTILINE_START = re.compile(
    r'^(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)'
    r'(?:\s*:\s*[^=]+)?\s*=\s*'
    r'(?:async\s*)?'
    r'(?:React\.(?:memo|forwardRef)\s*\()?'
    r'(?:useCallback\s*\()?\s*'
    r'(?:<[^>]+>\s*)?'
    r'\('
)

# 5. Method definition (Java, C#, C++, TS, PHP)
METHOD_REGEX = re.compile(
    r'^(?:(?:public|private|protected|internal|open|static|final|native|synchronized|abstract|async|override|virtual|inline|explicit|const|get|set|suspend|mutating)\s+)*'
    r'(?:[a-zA-Z0-9_<>\[\],.?*&:]+\s+)*'
    r'([a-zA-Z0-9_$:~]+)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*'
    r'(?:const\s+|override\s+|final\s+|noexcept\s+|throws\s+[^{]+|->\s*[^{]+|:\s*[^{]+)*\{'
)

# 5b. Multi-line / Allman-style method header (no '{' on first line; requires at least one modifier/type or trailing '(')
METHOD_MULTILINE_REGEX = re.compile(
    r'^(?:(?:public|private|protected|internal|open|static|final|native|synchronized|abstract|async|override|virtual|inline|explicit|const|get|set|suspend|mutating)\s+)*'
    r'(?:[a-zA-Z0-9_<>\[\],.?*&:]+\s+)+'
    r'([a-zA-Z0-9_$:~]+)\s*(?:<[^>]+>)?\s*\([^;]*$'
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

    # Design-token selectors matched on word boundaries so class names like
    # `.html-widget` or identifiers like `innerHTML` no longer masquerade as tokens.
    TOKEN_SELECTOR_REGEX = re.compile(r'(?<![\w.#-])(?::root|@theme|html|body)(?![\w-])')

    @staticmethod
    def _find_unescaped(s: str, ch: str, start: int) -> int:
        """Index of the next occurrence of ch not preceded by a backslash escape."""
        i = start
        while True:
            i = s.find(ch, i)
            if i == -1:
                return -1
            bs = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                bs += 1
                j -= 1
            if bs % 2 == 0:
                return i
            i += 1

    @classmethod
    def blank_template_literals(cls, lines):
        """
        Returns per-line copies with multi-line template-literal content blanked
        (single-line `...` pairs collapse to ``). Stateless over the whole buffer, so
        braces inside template strings can never corrupt the brace-depth tracker —
        unlike per-line state, this composes safely with the lexer being invoked
        more than once per line by the folding state machine.
        """
        out = []
        in_tmpl = False
        for line in lines:
            if in_tmpl:
                end = cls._find_unescaped(line, "`", 0)
                if end == -1:
                    out.append("")
                    continue
                in_tmpl = False
                line = line[end + 1:]
            res = []
            pos = 0
            while True:
                start = cls._find_unescaped(line, "`", pos)
                if start == -1:
                    res.append(line[pos:])
                    break
                end = cls._find_unescaped(line, "`", start + 1)
                if end == -1:
                    res.append(line[pos:start])
                    in_tmpl = True
                    break
                res.append(line[pos:start])
                res.append("``")
                pos = end + 1
            out.append("".join(res))
        return out

    CACHE_DIR = os.path.join(resolve_data_dir(), "skeletons")

    @classmethod
    def _collapse_import_lines(cls, skeleton: str, ext: str) -> str:
        """Collapses runs of >5 top-level import lines into a 1-line summary for surgical reads."""
        lines = skeleton.splitlines()
        out = []
        import_mods = []
        import_lines_buf = []
        in_import_block = False

        def flush_imports():
            non_blank_count = sum(1 for l in import_lines_buf if l.strip())
            if non_blank_count > 5:
                preview = ", ".join(import_mods[:8])
                if len(import_mods) > 8:
                    preview += f", +{len(import_mods) - 8}"
                comment_prefix = "#" if ext == ".py" else "//"
                out.append(f"{comment_prefix} [Collapsed {non_blank_count} imports: {preview}]")
            else:
                out.extend(import_lines_buf)
            import_mods.clear()
            import_lines_buf.clear()

        for line in lines:
            stripped = line.strip()
            is_imp = False
            mod = ""
            if ext == ".py":
                m1 = re.match(r"^import\s+([A-Za-z0-9_.,\s]+)", stripped)
                m2 = re.match(r"^from\s+([A-Za-z0-9_.]+)\s+import\b", stripped)
                if m1:
                    is_imp = True
                    mod = m1.group(1).split(",")[0].split(" as ")[0].strip()
                elif m2:
                    is_imp = True
                    mod = m2.group(1).strip()
            else:
                m3 = re.match(r"""^import\s+.*?\s+from\s+['"]([^'"]+)['"]""", stripped)
                if m3:
                    is_imp = True
                    mod = m3.group(1).strip()

            if is_imp:
                in_import_block = True
                import_lines_buf.append(line)
                if mod and mod not in import_mods:
                    import_mods.append(mod)
            else:
                if in_import_block:
                    if not stripped:
                        import_lines_buf.append(line)
                        continue
                    flush_imports()
                    in_import_block = False
                out.append(line)

        if import_lines_buf:
            flush_imports()
        return "\n".join(out)

    @classmethod
    def skeletonize_code(
        cls,
        code: str,
        file_ext: str,
        keep_symbols: Optional[Set[str]] = None,
        collapse_imports: bool = False,
    ) -> str:
        """Skeletons source code based on file extension, optionally preserving keep_symbols."""
        ext = file_ext.lower().lstrip(".")
        ext = f".{ext}" if ext else ""
        if ext == ".py":
            res = cls._skeletonize_python(code, keep_symbols=keep_symbols)
            return cls._collapse_import_lines(res, ext) if (collapse_imports and keep_symbols) else res
        elif ext in (
            ".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs",
            ".go", ".java", ".rs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
            ".cs", ".php", ".kt", ".kts", ".swift",
        ):
            res = None
            try:
                from src.treesitter_backend import TreeSitterBackend
                res = TreeSitterBackend.skeletonize_braces(code, ext, keep_symbols=keep_symbols)
            except Exception:
                res = None
            if res is None:
                res = cls._skeletonize_braces(code, ext, keep_symbols=keep_symbols)
            return cls._collapse_import_lines(res, ext) if (collapse_imports and keep_symbols) else res
        elif ext in (".css", ".scss", ".sass", ".less"):
            return cls._skeletonize_css(code, ext, keep_symbols=keep_symbols)
        return code

    @classmethod
    def _skeletonize_python(cls, code: str, keep_symbols: Optional[Set[str]] = None) -> str:
        """Uses Python standard library `ast` to fold function bodies cleanly."""
        try:
            tree = ast.parse(code)
            # When targeting specific symbols, condense verbose module docstring to 1 line
            if keep_symbols and tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
                doc_val = str(tree.body[0].value.value)
                first_line = doc_val.strip().split("\n")[0].strip()
                tree.body[0].value.value = f"{first_line} ..."

            def _fold_py_node(node: ast.AST, class_name: Optional[str] = None):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef):
                        _fold_py_node(child, class_name=child.name)
                    elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qual_name = f"{class_name}.{child.name}" if class_name else child.name
                        if keep_symbols is not None and (child.name in keep_symbols or qual_name in keep_symbols):
                            _fold_py_node(child, class_name=class_name)
                            continue
                        child.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
                    else:
                        _fold_py_node(child, class_name=class_name)

            _fold_py_node(tree)
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
            # Multi-line parameter list MUST have unclosed parentheses on this line (unless '=>' is present)
            if line_clean.count("(") > line_clean.count(")") or "=>" in line_clean:
                return (m.group(1), "arrow_multiline")

        m = METHOD_REGEX.match(line_clean)
        if m:
            name = m.group(1)
            clean_name = name.split("::")[-1]
            if clean_name not in CONTROL_KEYWORDS:
                return (clean_name, "method")

        if not line_clean.endswith(";"):
            m = METHOD_MULTILINE_REGEX.match(line_clean)
            if m:
                name = m.group(1)
                clean_name = name.split("::")[-1]
                if clean_name not in CONTROL_KEYWORDS and clean_name not in ("new", "delete", "sizeof"):
                    return (clean_name, "method_multiline")

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
        # Pre-blank multi-line template literals once per buffer (stateless) so braces
        # inside them cannot skew the folding depth tracker.
        count_src_lines = cls.blank_template_literals(lines)
        out = []

        lexer = BraceLexer()
        in_folding = False
        fold_depth = 0
        current_depth = 0

        pending_header_lines = []
        pending_name = None
        pending_kind = None
        arrow_seen = False

        comment_token = "/* ... */" if ext != ".py" else "..."

        for raw_line, count_src in zip(lines, count_src_lines):
            clean_line = lexer.clean_line(count_src).strip()

            # 1. If currently skipping a folded function body
            if in_folding:
                open_b, close_b = lexer.count_braces(count_src)
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

                if pending_kind == "arrow_multiline":
                    if "=>" in clean_line:
                        arrow_seen = True

                    has_body_brace = False
                    body_brace_idx = -1
                    if arrow_seen:
                        if "=>" in clean_line:
                            arrow_idx = clean_line.find("=>")
                            body_brace_idx = clean_line.find("{", arrow_idx + 2)
                            if body_brace_idx != -1:
                                has_body_brace = True
                        elif "{" in clean_line:
                            body_brace_idx = clean_line.find("{")
                            has_body_brace = True

                    if has_body_brace:
                        fn_name = pending_name
                        should_keep = keep_symbols is not None and fn_name in keep_symbols

                        sig_lines = pending_header_lines[:-1] + [raw_line[:body_brace_idx]]
                        temp_lexer = BraceLexer()
                        sig_open = sum(temp_lexer.count_braces(sl)[0] for sl in sig_lines)
                        sig_close = sum(temp_lexer.count_braces(sl)[1] for sl in sig_lines)
                        base_depth = current_depth + (sig_open - sig_close)

                        if should_keep:
                            out.extend(pending_header_lines)
                            for phl in pending_header_lines:
                                op, cl = lexer.count_braces(phl)
                                current_depth += op - cl
                        else:
                            folded_head = raw_line[:body_brace_idx] + f"{{ {comment_token} }}"
                            if raw_line.rstrip().endswith(";"):
                                folded_head += ";"
                            elif raw_line.rstrip().endswith(","):
                                folded_head += ","
                            pending_header_lines[-1] = folded_head
                            out.extend(pending_header_lines)
                            fold_depth = base_depth
                            current_depth = base_depth + 1
                            in_folding = True

                        pending_header_lines = []
                        pending_name = None
                        pending_kind = None
                        arrow_seen = False
                        continue
                    elif ";" in clean_line and not has_body_brace:
                        # Non-function variable assignment or ternary expression (e.g. gramBalanceDeduct)
                        out.extend(pending_header_lines)
                        for phl in pending_header_lines:
                            op, cl = lexer.count_braces(phl)
                            current_depth += op - cl
                        pending_header_lines = []
                        pending_name = None
                        pending_kind = None
                        arrow_seen = False
                        continue
                    elif len(pending_header_lines) > 25:
                        out.extend(pending_header_lines)
                        for phl in pending_header_lines:
                            op, cl = lexer.count_braces(phl)
                            current_depth += op - cl
                        pending_header_lines = []
                        pending_name = None
                        pending_kind = None
                        arrow_seen = False
                        continue
                    else:
                        continue
                else:
                    # Non-arrow multiline (function foo(, method, func)
                    if "{" in clean_line:
                        body_brace_idx = raw_line.rfind("{")
                        fn_name = pending_name
                        should_keep = keep_symbols is not None and fn_name in keep_symbols

                        sig_lines = pending_header_lines[:-1] + [raw_line[:body_brace_idx]]
                        temp_lexer = BraceLexer()
                        sig_open = sum(temp_lexer.count_braces(sl)[0] for sl in sig_lines)
                        sig_close = sum(temp_lexer.count_braces(sl)[1] for sl in sig_lines)
                        base_depth = current_depth + (sig_open - sig_close)

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
                    elif ";" in clean_line and "{" not in clean_line:
                        out.extend(pending_header_lines)
                        for phl in pending_header_lines:
                            op, cl = lexer.count_braces(phl)
                            current_depth += op - cl
                        pending_header_lines = []
                        pending_name = None
                        pending_kind = None
                        continue
                    elif len(pending_header_lines) > 25:
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

                if kind == 'arrow_multiline':
                    clean = lexer.clean_line(raw_line)
                    if "=>" in clean and clean.find("{") > clean.find("=>"):
                        kind = 'arrow_fn'
                    else:
                        pending_header_lines = [raw_line]
                        pending_name = name
                        pending_kind = 'arrow_multiline'
                        arrow_seen = "=>" in clean
                        continue

                if kind == 'arrow_fn' and "=>" not in raw_line:
                    pending_header_lines = [raw_line]
                    pending_name = name
                    pending_kind = 'arrow_multiline'
                    arrow_seen = False
                    continue

                if "{" in raw_line:
                    should_keep = keep_symbols is not None and name in keep_symbols
                    open_b, close_b = lexer.count_braces(count_src)

                    if should_keep:
                        out.append(raw_line)
                        current_depth += open_b - close_b
                    else:
                        folded_line = re.sub(r'\{.*$', f'{{ {comment_token} }}', raw_line)
                        if raw_line.rstrip().endswith(";"):
                            folded_line += ";"
                        out.append(folded_line)
                        head_src = count_src[:count_src.find("{") + 1] if "{" in count_src else count_src
                        open_b_head, _ = lexer.count_braces(head_src)
                        fold_depth = current_depth + open_b_head - 1
                        current_depth += open_b - close_b
                        if current_depth > fold_depth:
                            in_folding = True
                    continue

                if kind in ('fn_decl', 'go_func', 'rust_fn', 'method_multiline') and not raw_line.rstrip().endswith(";"):
                    pending_header_lines = [raw_line]
                    pending_name = name
                    pending_kind = 'fn_multiline'
                    continue

            # 4. Normal line
            out.append(raw_line)
            open_b, close_b = lexer.count_braces(count_src)
            current_depth += open_b - close_b

        if pending_header_lines:
            out.extend(pending_header_lines)

        return "\n".join(out)

    @classmethod
    def _skeletonize_css(cls, code: str, ext: str, keep_symbols: Optional[Set[str]] = None) -> str:
        """
        Specialized CSS / SCSS / SASS / LESS AST skeletonizer.
        Design tokens (:root, @theme, html, body, $variables, @variables) are ALWAYS preserved in full
        so AI agents always possess the complete project design palette and variables.
        Container at-rules (@media, @supports, @container, @layer) preserve structure and track depth.
        Component selectors and mixins are folded into '{ /* ... */ }' unless matching keep_symbols.
        """
        if ext == ".sass" and "{" not in code:
            return code

        lines = code.splitlines()
        out = []
        lexer = BraceLexer()
        in_folding = False
        fold_depth = 0
        current_depth = 0

        CONTAINER_RULES = ("@media", "@supports", "@container", "@layer", "@document")
        TOKEN_SELECTORS = (":root", "@theme", "html", "body")

        pending_sel = []

        for line in lines:
            raw_line = line
            clean_line = lexer.clean_line(line).strip()

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

            if pending_sel:
                pending_sel.append(raw_line)
                if "{" in clean_line:
                    full_sel = " ".join(s.strip() for s in pending_sel)
                    brace_idx = raw_line.find("{")
                    is_container = any(full_sel.strip().startswith(c) for c in CONTAINER_RULES)
                    is_token = bool(cls.TOKEN_SELECTOR_REGEX.search(full_sel))

                    matched_symbol = False
                    if keep_symbols:
                        for sym in keep_symbols:
                            clean_sym = sym.lstrip(".#")
                            if clean_sym and (clean_sym in full_sel or sym in full_sel):
                                matched_symbol = True
                                break

                    should_keep = is_container or is_token or matched_symbol
                    if should_keep:
                        out.extend(pending_sel)
                        for pl in pending_sel:
                            op, cl = lexer.count_braces(pl)
                            current_depth += op - cl
                    else:
                        head = raw_line[:brace_idx] + "{ /* ... */ }"
                        if raw_line.rstrip().endswith(";"):
                            head += ";"
                        pending_sel[-1] = head
                        out.extend(pending_sel)
                        open_b_head, _ = lexer.count_braces(raw_line[:brace_idx + 1])
                        fold_depth = current_depth + open_b_head - 1
                        open_b, close_b = lexer.count_braces(raw_line)
                        current_depth += open_b - close_b
                        if current_depth > fold_depth:
                            in_folding = True
                    pending_sel = []
                elif ";" in clean_line and "{" not in clean_line:
                    out.extend(pending_sel)
                    for pl in pending_sel:
                        op, cl = lexer.count_braces(pl)
                        current_depth += op - cl
                    pending_sel = []
                continue

            if "{" in clean_line:
                is_container = any(clean_line.startswith(c) for c in CONTAINER_RULES)
                is_token = bool(cls.TOKEN_SELECTOR_REGEX.search(clean_line))

                matched_symbol = False
                if keep_symbols:
                    for sym in keep_symbols:
                        clean_sym = sym.lstrip(".#")
                        if clean_sym and (clean_sym in clean_line or sym in clean_line):
                            matched_symbol = True
                            break

                should_keep = is_container or is_token or matched_symbol
                open_b, close_b = lexer.count_braces(raw_line)

                if should_keep:
                    out.append(raw_line)
                    current_depth += open_b - close_b
                else:
                    brace_idx = raw_line.find("{")
                    folded = raw_line[:brace_idx] + "{ /* ... */ }"
                    if raw_line.rstrip().endswith(";"):
                        folded += ";"
                    out.append(folded)
                    open_b_head, _ = lexer.count_braces(raw_line[:brace_idx + 1])
                    fold_depth = current_depth + open_b_head - 1
                    current_depth += open_b - close_b
                    if current_depth > fold_depth:
                        in_folding = True
                continue

            # Closing braces or standalone operators
            if clean_line.startswith("}"):
                out.append(raw_line)
                open_b, close_b = lexer.count_braces(raw_line)
                current_depth += open_b - close_b
                continue

            if clean_line and not clean_line.endswith(";") and not clean_line.startswith("//") and not clean_line.startswith("/*") and not clean_line.startswith("*"):
                pending_sel = [raw_line]
                continue

            out.append(raw_line)
            open_b, close_b = lexer.count_braces(raw_line)
            current_depth += open_b - close_b

        if pending_sel:
            out.extend(pending_sel)

        return "\n".join(out)

    @classmethod
    def get_skeleton_file(cls, original_path: str, target_symbol: Optional[str] = None, dep_mode: str = "signature") -> Tuple[Optional[str], int, int]:
        """
        Reads original_path, checks mtime cache, generates skeleton if modified or absent.
        If target_symbol is provided, applies Graph-Guided AST skeletonization retaining 1-hop dependencies.
        dep_mode: 'signature' (default; 1-hop external deps collapse to one-line signatures —
        mirrors the smart_read_file contract) or 'full' (legacy: 1-hop bodies kept intact).
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
        sym_suffix = f"_{target_symbol}_{dep_mode}" if target_symbol else ""
        skeleton_filename = f"{path_hash}{sym_suffix}_{base_name}"
        skeleton_path = os.path.join(cls.CACHE_DIR, skeleton_filename)
        meta_path = os.path.join(cls.CACHE_DIR, f"{path_hash}{sym_suffix}_{base_name}.meta.json")

        # 1. Check incremental mtime cache
        if os.path.exists(skeleton_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                    # mtime alone can miss a same-tick rewrite; size guards against it
                    if meta.get("mtime") == current_mtime and meta.get("size") == file_size:
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
                graph_content = graph.graph_guided_skeletonize(original_path, target_symbol=target_symbol, dep_mode=dep_mode)
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
                "size": file_size,
                "orig_tokens": orig_tokens,
                "skel_tokens": skel_tokens,
                "lines_count": lines_count
            }, mf)

        return skeleton_path, orig_tokens, skel_tokens
