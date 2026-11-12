"""
TokenSafe Graph Engine: Dependency-Aware Graph Analysis & Skeletonization.
Builds a zero-LLM deterministic code graph for Python, TypeScript/JavaScript, Go,
Rust, Java, C#, C/C++, PHP, Kotlin, Ruby, and Swift.

Uses the optional Tree-sitter backend when available (`src.treesitter_backend`)
and falls back seamlessly to the Python standard library `ast` and `BraceLexer`
state-machine parsers with zero extra required runtime dependencies.

Powers:
1. Graph-Guided Skeletonization (target + 1-hop full/signature, 0-hop isolated).
2. God Nodes Detection (architectural hubs via import- & call-aware centrality).
3. API Surface Map Generation (tokensafe-out/API_SURFACE.md).
4. Incremental `(mtime, size)` scanning & optional persistent disk cache.
"""

import ast
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from typing import Dict, Set, List, Optional, Tuple, Any

from src.paths import resolve_data_dir

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "venv", ".venv",
    "__pycache__", ".idea", ".vscode", "data", "coverage", ".pytest_cache",
    "Library", "Applications", "Pictures", "Movies", "Music", "Downloads",
    ".cache", ".npm", ".bun", ".rustup", ".cargo", ".local", ".system_generated",
    "target", "tokensafe-out",
}

SUPPORTED_EXTS = {
    ".py", ".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".cs",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".php", ".kt", ".kts", ".swift", ".rb",
}

_SKIP_CALLS = {
    "if", "else", "for", "while", "do", "switch", "case", "catch", "try",
    "return", "throw", "yield", "await", "sizeof", "typeof", "instanceof",
    "new", "delete", "import", "export", "require", "print", "len", "str",
    "int", "float", "bool", "list", "dict", "set", "tuple", "range",
    "div", "span", "button", "input", "label", "form", "svg", "path",
    "true", "false", "null", "undefined", "None", "True", "False",
}

_CACHE_SCHEMA_VERSION = 2


def _is_test_file(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/").lower()
    base = os.path.basename(norm)
    parts = norm.split("/")
    if any(p in ("test", "tests", "__tests__", "spec", "specs") for p in parts[:-1]):
        return True
    return (
        base.startswith("test_")
        or base.endswith(("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js"))
    )

class CodeSymbol:
    """Represents a structural code entity (file, class, or function)."""
    __slots__ = ("name", "kind", "file_path", "line_start", "line_end", "signature", "body")

    def __init__(
        self,
        name: str,
        kind: str,
        file_path: str,
        line_start: int = 0,
        line_end: int = 0,
        signature: str = "",
        body: str = ""
    ):
        self.name = name
        self.kind = kind
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.signature = signature
        self.body = body

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "signature": self.signature
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeSymbol":
        return cls(
            name=data.get("name", ""),
            kind=data.get("kind", "function"),
            file_path=data.get("file_path", ""),
            line_start=int(data.get("line_start", 0)),
            line_end=int(data.get("line_end", 0)),
            signature=data.get("signature", ""),
        )


class CodeGraph:
    """
    Deterministic In-Memory Knowledge Graph of a software repository.
    Nodes: File (`file:...`), Module (`mod:...`), Symbol (`rel_path::qual_name`), Call (`call:...`).
    Edges: Directed dependencies (`CALLS`, `IMPORTS`, `CONTAINS`, resolved `file -> file`).
    """

    def __init__(self, root_dir: str = ".", use_disk_cache: bool = False):
        self.root_dir = os.path.abspath(root_dir)
        self.use_disk_cache = use_disk_cache
        self.symbols: Dict[str, CodeSymbol] = {}
        self.file_symbols: Dict[str, List[str]] = {}       # file_rel_path -> [symbol_keys]
        self.edges: Dict[str, Set[str]] = {}               # caller/importer -> set(callees/imported)
        self.rev_edges: Dict[str, Set[str]] = {}           # callee/imported -> set(callers/importers)
        self.file_mtimes: Dict[str, float] = {}            # file_rel_path -> mtime
        self.file_stats: Dict[str, Tuple[float, int]] = {} # file_rel_path -> (mtime, size)
        self.file_imports: Dict[str, Set[str]] = {}        # file_rel_path -> raw imported modules
        self.last_scanned: float = 0.0
        self.backend: str = self._detect_backend()

        if self.use_disk_cache and os.environ.get("TOKENSAFE_GRAPH_DISK_CACHE", "1") != "0":
            self._load_disk_cache()

    @staticmethod
    def _detect_backend() -> str:
        try:
            from src.treesitter_backend import TreeSitterBackend
            if TreeSitterBackend.is_available():
                return "treesitter"
        except Exception:
            pass
        return "stdlib"

    def _cache_path(self) -> str:
        cache_dir = os.path.join(resolve_data_dir(), "graph_cache")
        key = hashlib.md5(self.root_dir.encode("utf-8")).hexdigest()[:12]
        return os.path.join(cache_dir, f"{key}.json")

    def _load_disk_cache(self) -> bool:
        path = self._cache_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if (
                data.get("version") != _CACHE_SCHEMA_VERSION
                or data.get("root_dir") != self.root_dir
                or data.get("backend") != self.backend
            ):
                return False

            self.symbols = {k: CodeSymbol.from_dict(v) for k, v in data.get("symbols", {}).items()}
            self.file_symbols = {k: list(v) for k, v in data.get("file_symbols", {}).items()}
            self.edges = {k: set(v) for k, v in data.get("edges", {}).items()}
            self.rev_edges = {k: set(v) for k, v in data.get("rev_edges", {}).items()}
            self.file_stats = {
                k: (float(v[0]), int(v[1]))
                for k, v in data.get("file_stats", {}).items()
                if isinstance(v, (list, tuple)) and len(v) == 2
            }
            self.file_mtimes = {k: v[0] for k, v in self.file_stats.items()}
            self.file_imports = {k: set(v) for k, v in data.get("file_imports", {}).items()}
            self.last_scanned = float(data.get("last_scanned", 0.0))
            return True
        except Exception:
            self.symbols.clear()
            self.file_symbols.clear()
            self.edges.clear()
            self.rev_edges.clear()
            self.file_stats.clear()
            self.file_mtimes.clear()
            self.file_imports.clear()
            return False

    def _save_disk_cache(self) -> None:
        if not self.use_disk_cache or os.environ.get("TOKENSAFE_GRAPH_DISK_CACHE", "1") == "0":
            return
        path = self._cache_path()
        cache_dir = os.path.dirname(path)
        try:
            os.makedirs(cache_dir, exist_ok=True)
            payload = {
                "version": _CACHE_SCHEMA_VERSION,
                "root_dir": self.root_dir,
                "backend": self.backend,
                "last_scanned": self.last_scanned,
                "file_stats": {k: [v[0], v[1]] for k, v in self.file_stats.items()},
                "file_symbols": self.file_symbols,
                "file_imports": {k: sorted(v) for k, v in self.file_imports.items()},
                "symbols": {k: v.to_dict() for k, v in self.symbols.items()},
                "edges": {k: sorted(v) for k, v in self.edges.items()},
                "rev_edges": {k: sorted(v) for k, v in self.rev_edges.items()},
            }
            fd, tmp_path = tempfile.mkstemp(prefix="graph_", suffix=".tmp", dir=cache_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except OSError:
            pass

    def add_edge(self, source: str, target: str):
        if not source or not target or source == target:
            return
        if source not in self.edges:
            self.edges[source] = set()
        self.edges[source].add(target)

        if target not in self.rev_edges:
            self.rev_edges[target] = set()
        self.rev_edges[target].add(source)

    def _remove_edge(self, source: str, target: str):
        if source in self.edges:
            self.edges[source].discard(target)
        if target in self.rev_edges:
            self.rev_edges[target].discard(source)

    def scan(self, force: bool = False) -> int:
        """
        Fast incremental scan of the workspace.
        Parses modified files only (checked via `(mtime, size)`); returns number of files re-indexed.
        """
        system_roots = {"/", "/Users", "/home", "/root", os.path.expanduser("~")}
        if self.root_dir in system_roots:
            return 0

        current_backend = self._detect_backend()
        if current_backend != self.backend:
            self.backend = current_backend
            force = True

        reindexed = 0
        current_files = set()

        for dirpath, dirnames, filenames in os.walk(self.root_dir):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for filename in filenames:
                _, ext = os.path.splitext(filename)
                if ext.lower() in SUPPORTED_EXTS:
                    full_path = os.path.join(dirpath, filename)
                    rel_path = os.path.relpath(full_path, self.root_dir).replace("\\", "/")
                    current_files.add(rel_path)

                    try:
                        st = os.stat(full_path)
                        if not stat.S_ISREG(st.st_mode) or st.st_size > 2 * 1024 * 1024:
                            continue
                        sig = (st.st_mtime, st.st_size)
                    except OSError:
                        continue

                    prev_sig = self.file_stats.get(rel_path)
                    prev_mtime = self.file_mtimes.get(rel_path)
                    changed = (
                        force
                        or prev_sig is None
                        or prev_sig != sig
                        or prev_mtime is None
                        or prev_mtime < st.st_mtime
                    )
                    if changed:
                        self._index_file(full_path, rel_path)
                        self.file_stats[rel_path] = sig
                        self.file_mtimes[rel_path] = st.st_mtime
                        reindexed += 1

        # Purge removed files
        deleted = (set(self.file_stats.keys()) | set(self.file_mtimes.keys())) - current_files
        for del_path in deleted:
            self._purge_file(del_path)
            self.file_stats.pop(del_path, None)
            self.file_mtimes.pop(del_path, None)

        if reindexed > 0 or deleted or force:
            self._resolve_local_imports()

        self.last_scanned = time.time()
        if reindexed > 0 or deleted:
            self._save_disk_cache()
        return reindexed

    def _purge_file(self, rel_path: str):
        """Removes symbols, imports, and edges belonging to a file."""
        sym_keys = self.file_symbols.pop(rel_path, [])
        self.file_imports.pop(rel_path, None)
        for k in sym_keys:
            self.symbols.pop(k, None)
            targets = self.edges.pop(k, set())
            for t in targets:
                if t in self.rev_edges:
                    self.rev_edges[t].discard(k)
            sources = self.rev_edges.pop(k, set())
            for s in sources:
                if s in self.edges:
                    self.edges[s].discard(k)

        # File-level node
        file_node = f"file:{rel_path}"
        for t in self.edges.pop(file_node, set()):
            if t in self.rev_edges:
                self.rev_edges[t].discard(file_node)
        for s in self.rev_edges.pop(file_node, set()):
            if s in self.edges:
                self.edges[s].discard(file_node)

    def _index_file(self, full_path: str, rel_path: str):
        """Indexes a single code file using Tree-sitter (if available) or stdlib parsers."""
        self._purge_file(rel_path)
        try:
            st = os.stat(full_path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > 2 * 1024 * 1024:
                return
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return

        file_node = f"file:{rel_path}"
        self.file_symbols[rel_path] = []
        self.file_imports[rel_path] = set()

        _, ext = os.path.splitext(full_path)
        ext = ext.lower()

        if self._try_index_treesitter(content, rel_path, file_node, ext):
            return

        if ext == ".py":
            self._parse_python(content, rel_path, file_node)
        elif ext == ".rb":
            self._parse_ruby(content, rel_path, file_node)
        elif ext in SUPPORTED_EXTS:
            self._parse_braces(content, rel_path, file_node, ext)

    def _register_symbol(self, rel_path: str, file_node: str, symbol: CodeSymbol, sym_key: str):
        self.symbols[sym_key] = symbol
        if sym_key not in self.file_symbols[rel_path]:
            self.file_symbols[rel_path].append(sym_key)
        self.add_edge(file_node, sym_key)

    def _try_index_treesitter(self, code: str, rel_path: str, file_node: str, ext: str) -> bool:
        if self.backend != "treesitter":
            return False
        try:
            from src.treesitter_backend import TreeSitterBackend
            data = TreeSitterBackend.extract_graph_data(code, ext, rel_path)
        except Exception:
            return False
        if data is None:
            return False

        for mod in data.get("imports", []):
            self.file_imports[rel_path].add(mod)
            self.add_edge(file_node, f"mod:{mod}")
        for sym in data.get("imported_symbols", []):
            self.add_edge(file_node, f"sym:{sym}")

        for item in data.get("symbols", []):
            symbol = CodeSymbol(
                name=item["name"],
                kind=item["kind"],
                file_path=rel_path,
                line_start=item["line_start"],
                line_end=item["line_end"],
                signature=item["signature"],
            )
            self._register_symbol(rel_path, file_node, symbol, item["sym_key"])

        for caller_key, callee_name in data.get("calls", []):
            self.add_edge(caller_key, f"call:{callee_name}")

        return True

    def _parse_python(self, code: str, rel_path: str, file_node: str):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return

        lines = code.splitlines()

        def walk_py(node: ast.AST, class_name: Optional[str] = None, fn_key: Optional[str] = None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        self.file_imports[rel_path].add(alias.name)
                        self.add_edge(file_node, f"mod:{alias.name}")
                elif isinstance(child, ast.ImportFrom):
                    mod_prefix = "." * (child.level or 0)
                    mod_name = f"{mod_prefix}{child.module or ''}"
                    if mod_name:
                        self.file_imports[rel_path].add(mod_name)
                        self.add_edge(file_node, f"mod:{mod_name}")
                    for alias in child.names:
                        if alias.name != "*":
                            self.add_edge(file_node, f"sym:{alias.name}")
                elif isinstance(child, ast.ClassDef):
                    start = getattr(child, "lineno", 1)
                    end = getattr(child, "end_lineno", start)
                    qual = f"{class_name}::{child.name}" if class_name else child.name
                    sym_key = f"{rel_path}::{qual}"
                    sig = lines[start - 1].strip() if 0 < start <= len(lines) else child.name
                    symbol = CodeSymbol(
                        name=child.name,
                        kind="class",
                        file_path=rel_path,
                        line_start=start,
                        line_end=end,
                        signature=sig,
                    )
                    self._register_symbol(rel_path, file_node, symbol, sym_key)
                    walk_py(child, class_name=qual, fn_key=None)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = getattr(child, "lineno", 1)
                    end = getattr(child, "end_lineno", start)
                    qual = f"{class_name}::{child.name}" if class_name else child.name
                    sym_key = f"{rel_path}::{qual}"
                    sig = lines[start - 1].strip() if 0 < start <= len(lines) else child.name
                    symbol = CodeSymbol(
                        name=child.name,
                        kind="function",
                        file_path=rel_path,
                        line_start=start,
                        line_end=end,
                        signature=sig,
                    )
                    self._register_symbol(rel_path, file_node, symbol, sym_key)
                    walk_py(child, class_name=class_name, fn_key=sym_key)
                else:
                    if isinstance(child, ast.Call) and fn_key:
                        callee_name = None
                        if isinstance(child.func, ast.Name):
                            callee_name = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            callee_name = child.func.attr
                        if callee_name and callee_name not in _SKIP_CALLS:
                            self.add_edge(fn_key, f"call:{callee_name}")
                    walk_py(child, class_name=class_name, fn_key=fn_key)

        walk_py(tree)

    def _parse_braces(self, code: str, rel_path: str, file_node: str, ext: str):
        from src.skeletonizer import BraceLexer, Skeletonizer

        lines = code.splitlines()
        count_lines = Skeletonizer.blank_template_literals(lines)
        lexer = BraceLexer()

        # Strip comments only (keep string literals intact for import path extraction)
        no_block_comments = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        cleaned_text = re.sub(r"//.*$", "", no_block_comments, flags=re.MULTILINE)
        if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            imp_re = re.compile(
                r"""(?:(?:import|export)\s+(?:.*?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))"""
            )
            for m in imp_re.finditer(cleaned_text):
                mod = m.group(1) or m.group(2)
                if mod:
                    self.file_imports[rel_path].add(mod)
                    self.add_edge(file_node, f"mod:{mod}")
        elif ext == ".go":
            for m in re.finditer(r'^\s*import\s+(?:[A-Za-z_.]+\s+)?"([^"]+)"', cleaned_text, re.MULTILINE):
                self.file_imports[rel_path].add(m.group(1))
                self.add_edge(file_node, f"mod:{m.group(1)}")
            for blk in re.finditer(r'import\s*\((.*?)\)', cleaned_text, re.DOTALL):
                for m in re.finditer(r'"([^"]+)"', blk.group(1)):
                    self.file_imports[rel_path].add(m.group(1))
                    self.add_edge(file_node, f"mod:{m.group(1)}")
        elif ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp"):
            for m in re.finditer(r'^\s*#include\s+[<"]([^>"]+)[>"]', cleaned_text, re.MULTILINE):
                self.file_imports[rel_path].add(m.group(1))
                self.add_edge(file_node, f"mod:{m.group(1)}")
        else:
            for m in re.finditer(r'^\s*(?:pub\s+)?(?:use|import|using)\s+([^;\n]+)', cleaned_text, re.MULTILINE):
                mod = m.group(1).strip().rstrip(";")
                if mod:
                    self.file_imports[rel_path].add(mod)
                    self.add_edge(file_node, f"mod:{mod}")

        container_re = re.compile(
            r'^(?:export\s+(?:default\s+)?)?(?:pub(?:\([^)]*\))?\s+|public\s+|private\s+|protected\s+|internal\s+|abstract\s+|final\s+|sealed\s+|open\s+|data\s+)*'
            r'(?:class|interface|struct|impl(?:\s+[A-Za-z0-9_<>,\s]+?\s+for)?|trait|enum|record|protocol|extension|namespace|type)\s+([A-Za-z0-9_]+)'
        )
        go_struct_re = re.compile(r'^type\s+([A-Za-z0-9_]+)\s+(?:struct|interface)\b')
        ts_method_re = re.compile(
            r'^(?:(?:public|private|protected|static|readonly|async|override|get|set)\s+)*([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?::\s*[^{;]+)?\{'
        )
        call_re = re.compile(r'\b([a-zA-Z_$][a-zA-Z0-9_$]{1,})\s*\(')

        depth = 0
        sym_stack: List[Tuple[str, int, str]] = []
        pending_header: Optional[Tuple[str, str, int, str]] = None

        for idx, (raw_line, count_src) in enumerate(zip(lines, count_lines), 1):
            clean = lexer.clean_line(count_src).strip()
            open_b, close_b = lexer.count_braces(count_src)

            matched_name: Optional[str] = None
            matched_kind: Optional[str] = None
            matched_sig = raw_line.strip()
            matched_start = idx

            if clean:
                cm = container_re.match(clean) or (go_struct_re.match(clean) if ext == ".go" else None)
                if cm:
                    matched_name = cm.group(1)
                    matched_kind = "class"
                else:
                    fn_info = Skeletonizer._extract_fn(clean)
                    if fn_info and fn_info[0] != "anonymous":
                        matched_name, raw_kind = fn_info
                        matched_kind = "function"
                        if raw_kind in ("arrow_multiline", "method_multiline", "fn_decl", "go_func", "rust_fn") and "{" not in clean:
                            if not clean.endswith(";"):
                                pending_header = (matched_name, matched_kind, idx, matched_sig)
                            matched_name = None
                    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs") and any(k == "class" for _, _, k in sym_stack):
                        mm = ts_method_re.match(clean)
                        if mm and mm.group(1) not in _SKIP_CALLS:
                            matched_name = mm.group(1)
                            matched_kind = "function"

            if pending_header and not matched_name:
                if "{" in clean:
                    matched_name, matched_kind, matched_start, matched_sig = pending_header
                    pending_header = None
                elif ";" in clean:
                    pending_header = None

            if matched_name and matched_kind:
                class_ctx = [sk.split("::", 1)[-1] for sk, _, k in sym_stack if k == "class"]
                qual = f"{class_ctx[-1]}::{matched_name}" if (class_ctx and matched_kind == "function") else matched_name
                sym_key = f"{rel_path}::{qual}"
                symbol = CodeSymbol(
                    name=matched_name,
                    kind=matched_kind,
                    file_path=rel_path,
                    line_start=matched_start,
                    line_end=idx,
                    signature=matched_sig,
                )
                self._register_symbol(rel_path, file_node, symbol, sym_key)
                if open_b > 0:
                    sym_stack.append((sym_key, depth + 1, matched_kind))

            active_fn = next((sk for sk, _, k in reversed(sym_stack) if k == "function"), None)
            if active_fn and clean:
                for cm in call_re.finditer(clean):
                    callee = cm.group(1)
                    if callee != matched_name and callee not in _SKIP_CALLS:
                        self.add_edge(active_fn, f"call:{callee}")

            depth += open_b - close_b
            while sym_stack and depth < sym_stack[-1][1]:
                finished_key, _, _ = sym_stack.pop()
                if finished_key in self.symbols:
                    self.symbols[finished_key].line_end = idx

    def _parse_ruby(self, code: str, rel_path: str, file_node: str):
        """Stdlib Ruby parser: keyword-delimited blocks (`def`/`class`/`module` ...
        `end`) instead of braces. Mirrors the brace-parser conventions (kinds,
        quals, call edges) so both backends stay in parity."""
        lines = code.splitlines()
        require_re = re.compile(r'^\s*require(?:_relative)?\s+["\']([^"\']+)["\']')
        opener_re = re.compile(
            r'^\s*(?:def|class|module|if|unless|while|until|case|for|begin)\b|\bdo\s*(?:\|[^|]*\|)?\s*$'
        )
        container_re = re.compile(r'^\s*(?:class|module)\s+([A-Z][A-Za-z0-9_:]*)')
        method_re = re.compile(r'^\s*def\s+(?:self\.)?([a-zA-Z_][a-zA-Z0-9_]*[?!]?)')
        call_re = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*[?!]?)\s*\(')
        end_re = re.compile(r'\bend\b')

        # Stack items: (sym_key|None, kind|None, qual|None). None entries track
        # non-symbol keyword blocks (if/while/do/...) so `end` counting balances.
        stack: List[Tuple[Optional[str], Optional[str], Optional[str]]] = []

        for idx, raw in enumerate(lines, 1):
            no_comment = raw.split("#", 1)[0]
            # Blank out string literals so `end`-like words inside strings never count
            no_str = re.sub(r'"[^"]*"', '""', no_comment)
            no_str = re.sub(r"'[^']*'", "''", no_str)
            if not no_str.strip():
                continue

            rm = require_re.match(raw)
            if rm:
                self.file_imports[rel_path].add(rm.group(1))
                self.add_edge(file_node, f"mod:{rm.group(1)}")

            opens = len(opener_re.findall(no_str))
            closes = len(end_re.findall(no_str))

            matched_name: Optional[str] = None
            matched_kind: Optional[str] = None
            cm = container_re.match(no_str)
            if cm:
                matched_kind, matched_name = "class", cm.group(1)
            else:
                mm = method_re.match(no_str)
                if mm:
                    matched_kind, matched_name = "function", mm.group(1)

            if matched_name and matched_kind:
                class_ctx = [e[2] for e in stack if e[1] == "class"]
                qual = f"{class_ctx[-1]}::{matched_name}" if class_ctx else matched_name
                sym_key = f"{rel_path}::{qual}"
                symbol = CodeSymbol(
                    name=matched_name,
                    kind=matched_kind,
                    file_path=rel_path,
                    line_start=idx,
                    line_end=idx,
                    signature=raw.strip(),
                )
                self._register_symbol(rel_path, file_node, symbol, sym_key)
                stack.append((sym_key, matched_kind, qual))
            elif opens:
                stack.append((None, None, None))

            active_fn = next((e for e in reversed(stack) if e[1] == "function"), None)
            if active_fn and active_fn[0]:
                for cmm in call_re.finditer(no_str):
                    callee = cmm.group(1)
                    if callee != matched_name and callee not in _SKIP_CALLS:
                        self.add_edge(active_fn[0], f"call:{callee}")

            for _ in range(closes):
                if not stack:
                    break
                entry = stack.pop()
                if entry[0]:
                    sym = self.symbols.get(entry[0])
                    if sym:
                        sym.line_end = idx

    def _resolve_local_imports(self) -> None:
        """Resolves raw `file_imports` strings to real `file:<rel_path>` edges."""
        indexed_files = set(self.file_symbols.keys())
        if not indexed_files:
            return

        for rel_path in indexed_files:
            src_node = f"file:{rel_path}"
            existing = [t for t in self.edges.get(src_node, set()) if t.startswith("file:")]
            for t in existing:
                self._remove_edge(src_node, t)

        dir_to_files: Dict[str, List[str]] = {}
        for fp in indexed_files:
            d = os.path.dirname(fp)
            dir_to_files.setdefault(d, []).append(fp)

        for rel_path, mods in self.file_imports.items():
            src_node = f"file:{rel_path}"
            src_dir = os.path.dirname(rel_path)
            ext = os.path.splitext(rel_path)[1].lower()

            for mod in mods:
                for target_rel in self._resolve_single_import(mod, src_dir, ext, indexed_files, dir_to_files):
                    if target_rel != rel_path:
                        self.add_edge(src_node, f"file:{target_rel}")

    @staticmethod
    def _resolve_single_import(
        mod: str,
        src_dir: str,
        ext: str,
        indexed_files: Set[str],
        dir_to_files: Dict[str, List[str]],
    ) -> List[str]:
        if not mod:
            return []

        if ext == ".py":
            if mod.startswith("."):
                dots = len(mod) - len(mod.lstrip("."))
                rest = mod[dots:].replace(".", "/")
                base_dir = src_dir
                for _ in range(dots - 1):
                    base_dir = os.path.dirname(base_dir)
                prefix = os.path.normpath(os.path.join(base_dir, rest)).replace("\\", "/") if rest else base_dir
            else:
                prefix = mod.replace(".", "/")
            prefix = prefix.lstrip("./")
            for cand in (f"{prefix}.py", f"{prefix}/__init__.py"):
                if cand in indexed_files:
                    return [cand]
            return []

        if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            if not mod.startswith("."):
                return []
            base = os.path.normpath(os.path.join(src_dir, mod)).replace("\\", "/").lstrip("./")
            for cand in (
                base,
                f"{base}.ts", f"{base}.tsx", f"{base}.js", f"{base}.jsx", f"{base}.mjs", f"{base}.cjs",
                f"{base}/index.ts", f"{base}/index.tsx", f"{base}/index.js", f"{base}/index.mjs",
            ):
                if cand in indexed_files:
                    return [cand]
            return []

        if ext == ".go":
            mod_clean = mod.strip("/")
            for d, files in dir_to_files.items():
                if d and (mod_clean == d or mod_clean.endswith(f"/{d}")):
                    return [f for f in files if f.endswith(".go")]
            return []

        if ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp"):
            cand = os.path.normpath(os.path.join(src_dir, mod)).replace("\\", "/").lstrip("./")
            return [cand] if cand in indexed_files else []

        if ext == ".rs" and mod.startswith(("crate::", "super::", "self::")):
            parts = mod.split("::")[1:]
            if parts:
                rel = "/".join(parts)
                for cand in (f"{rel}.rs", f"{rel}/mod.rs", f"src/{rel}.rs", f"src/{rel}/mod.rs"):
                    if cand in indexed_files:
                        return [cand]
            return []

        return []

    # -------------------------------------------------------------
    # 1. God Nodes Analysis (Import & Call Centrality)
    # -------------------------------------------------------------
    def get_god_nodes(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Calculates architectural hub centrality across files.
        Prioritizes resolved file-to-file imports and import-verified symbol calls,
        and downweights test files when production files are present.
        """
        has_prod_files = any(not _is_test_file(p) for p in self.file_symbols.keys())
        has_resolved_imports = any(
            t.startswith("file:")
            for src, targets in self.edges.items()
            if src.startswith("file:")
            for t in targets
        )

        scores: Dict[str, Dict[str, Any]] = {}

        for rel_path in self.file_symbols.keys():
            file_node = f"file:{rel_path}"
            out_degree = len(self.edges.get(file_node, set()))

            importing_files = {
                s[5:] for s in self.rev_edges.get(file_node, set()) if s.startswith("file:")
            }
            in_degree = len(importing_files)

            for sym_key in self.file_symbols.get(rel_path, []):
                sym = self.symbols.get(sym_key)
                sym_name = sym.name if sym else sym_key.split("::")[-1].split(".")[-1]
                for caller_key in self.rev_edges.get(f"call:{sym_name}", set()):
                    caller_file = caller_key.split("::", 1)[0]
                    if caller_file == rel_path:
                        continue
                    if not has_resolved_imports or caller_file in importing_files:
                        in_degree += 1

            raw_centrality = in_degree * 2 + out_degree
            rank_score = raw_centrality * 0.2 if (has_prod_files and _is_test_file(rel_path)) else float(raw_centrality)
            scores[rel_path] = {
                "file": rel_path,
                "centrality": raw_centrality,
                "dependents": in_degree,
                "dependencies": out_degree,
                "symbols_count": len(self.file_symbols.get(rel_path, [])),
                "_rank": rank_score,
            }

        ranked = sorted(scores.values(), key=lambda x: (x["_rank"], x["centrality"]), reverse=True)
        result = []
        for item in ranked[:top_n]:
            item.pop("_rank", None)
            result.append(item)
        return result

    # -------------------------------------------------------------
    # 2. Neighborhood & Graph-Guided Skeletonization
    # -------------------------------------------------------------
    def _match_target_keys(self, target_symbol: str, target_file: Optional[str] = None) -> List[str]:
        if target_symbol in self.symbols:
            return [target_symbol]

        norm_target = target_symbol.replace(".", "::")
        candidates = []
        for k, sym in self.symbols.items():
            if sym.name == target_symbol or k.endswith(f"::{target_symbol}") or k.endswith(f"::{norm_target}"):
                candidates.append(k)

        if target_file:
            in_file = [k for k in candidates if k.startswith(f"{target_file}::")]
            if in_file:
                return in_file
        return candidates

    def _resolve_callee_keys(self, callee_name: str, caller_file: str) -> List[str]:
        imported_files = {
            t[5:] for t in self.edges.get(f"file:{caller_file}", set()) if t.startswith("file:")
        }
        same_file = []
        imported = []
        others = []

        for k, sym in self.symbols.items():
            if sym.name == callee_name or k.endswith(f"::{callee_name}"):
                if sym.file_path == caller_file:
                    same_file.append(k)
                elif sym.file_path in imported_files:
                    imported.append(k)
                else:
                    others.append(k)

        if same_file or imported:
            return same_file + imported
        return others if len(others) <= 3 else []

    def get_neighborhood(
        self,
        target_symbol: str,
        target_file: Optional[str] = None,
        include_two_hop: bool = False,
    ) -> Dict[str, Set[str]]:
        """
        Returns 0-hop (`target`), 1-hop (`one_hop`), and optionally 2-hop (`two_hop`) related symbols.
        Prioritizes same-file and imported-file edges to eliminate bare-name collisions.
        """
        target_keys = self._match_target_keys(target_symbol, target_file=target_file)
        one_hop: Set[str] = set()

        def _expand_one(tk: str) -> Set[str]:
            found: Set[str] = set()
            sym = self.symbols.get(tk)
            caller_file = sym.file_path if sym else tk.split("::", 1)[0]
            short_name = sym.name if sym else tk.split("::")[-1].split(".")[-1]

            for callee in self.edges.get(tk, set()):
                if callee.startswith("call:"):
                    found.update(self._resolve_callee_keys(callee[5:], caller_file))
                elif callee in self.symbols:
                    found.add(callee)

            importing_files = {
                s[5:] for s in self.rev_edges.get(f"file:{caller_file}", set()) if s.startswith("file:")
            }
            scoped_callers = []
            fallback_callers = []
            for caller in self.rev_edges.get(f"call:{short_name}", set()):
                if caller not in self.symbols or caller == tk:
                    continue
                cf = self.symbols[caller].file_path
                if cf == caller_file or cf in importing_files:
                    scoped_callers.append(caller)
                else:
                    fallback_callers.append(caller)
            if scoped_callers:
                found.update(scoped_callers)
            elif len(fallback_callers) <= 3:
                found.update(fallback_callers)

            found.discard(tk)
            return found

        for tk in target_keys:
            one_hop.update(_expand_one(tk))
        one_hop -= set(target_keys)

        result: Dict[str, Set[str]] = {
            "target": set(target_keys),
            "one_hop": one_hop,
        }

        if include_two_hop:
            two_hop: Set[str] = set()
            for ok in one_hop:
                two_hop.update(_expand_one(ok))
            two_hop -= set(target_keys)
            two_hop -= one_hop
            result["two_hop"] = two_hop

        return result

    def graph_guided_skeletonize(
        self,
        full_path: str,
        target_symbol: Optional[str] = None,
        include_deps: bool = True,
        dep_mode: str = "full",
        collapse_imports: bool = False,
    ) -> str:
        """
        Surgically folds code based on graph connectivity:
        - target_symbol: Retains FULL function body.
        - 1-hop dependencies:
            * include_deps=True, dep_mode='full': retains full 1-hop bodies in file.
            * include_deps=True, dep_mode='signature': folds 1-hop bodies into signatures
              and prepends compact 1-line external dependency signatures.
            * include_deps=False: 0-hop surgical single-symbol isolation.
        - 2-hop / Unrelated functions in the same file: Stripped to AST signatures (ellipsis).
        """
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except OSError:
            return ""

        from src.skeletonizer import Skeletonizer
        _, ext = os.path.splitext(full_path)
        ext = ext.lower()

        if not target_symbol:
            return Skeletonizer.skeletonize_code(code, ext, collapse_imports=collapse_imports)

        try:
            target_rel = os.path.relpath(os.path.abspath(full_path), self.root_dir).replace("\\", "/")
        except ValueError:
            target_rel = ""

        keep_full_names: Set[str] = {target_symbol}
        neighborhood = self.get_neighborhood(target_symbol, target_file=target_rel or None)
        for tk in neighborhood.get("target", set()):
            qual = tk.split("::", 1)[-1]
            keep_full_names.add(qual)
            keep_full_names.add(qual.replace("::", "."))
            sym = self.symbols.get(tk)
            if sym:
                keep_full_names.add(sym.name)

        external_dep_lines: List[str] = []
        mode = (dep_mode or "full").strip().lower()

        if include_deps:
            one_hop = sorted(neighborhood.get("one_hop", set()))
            if mode == "signature":
                comment_prefix = "#" if ext == ".py" else "//"
                for k in one_hop:
                    sym = self.symbols.get(k)
                    if sym and sym.file_path != target_rel and len(external_dep_lines) < 10:
                        external_dep_lines.append(
                            f"{comment_prefix}   - {sym.file_path} (L{sym.line_start}): {sym.signature}"
                        )
            else:
                for k in one_hop:
                    qual = k.split("::", 1)[-1]
                    keep_full_names.add(qual)
                    keep_full_names.add(qual.replace("::", "."))
                    sym = self.symbols.get(k)
                    if sym:
                        keep_full_names.add(sym.name)

        skel = Skeletonizer.skeletonize_code(
            code,
            ext,
            keep_symbols=keep_full_names,
            collapse_imports=collapse_imports,
        )
        if external_dep_lines:
            comment_prefix = "#" if ext == ".py" else "//"
            dep_header = f"{comment_prefix} 1-hop external dependencies (signatures):\n" + "\n".join(external_dep_lines)
            return dep_header + "\n\n" + skel
        return skel

    # -------------------------------------------------------------
    # 3. API Surface Generator (tokensafe-out/API_SURFACE.md)
    # -------------------------------------------------------------
    def generate_api_surface(self, output_path: Optional[str] = None) -> str:
        """
        Generates a high-density, agent-friendly Markdown map of all classes,
        types, and exported interfaces in the project (~1,500 tokens).
        """
        god_nodes = self.get_god_nodes(top_n=5)
        lines = [
            "# TokenSafe Architecture & API Surface",
            f"*Generated locally at {time.strftime('%Y-%m-%d %H:%M:%S')} • Zero LLM Cost*",
            "",
            "## Architectural Hubs (God Nodes)",
            "The foundational modules that connect the largest portion of the project:",
            ""
        ]

        for g in god_nodes:
            lines.append(f"- **`{g['file']}`**: {g['dependents']} dependents, {g['dependencies']} dependencies ({g['symbols_count']} exported symbols)")

        lines.extend([
            "",
            "## Interface Surface Map",
            ""
        ])

        for rel_path, sym_keys in sorted(self.file_symbols.items()):
            if not sym_keys:
                continue
            lines.append(f"### `{rel_path}`")
            for k in sym_keys:
                sym = self.symbols.get(k)
                if sym:
                    lines.append(f"- `{sym.kind}` **`{sym.name}`** (line {sym.line_start}): `{sym.signature}`")
            lines.append("")

        output_content = "\n".join(lines)

        if output_path is None:
            out_dir = os.path.join(self.root_dir, "tokensafe-out")
            output_path = os.path.join(out_dir, "API_SURFACE.md")

        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output_content)
        except OSError:
            pass

        return output_content

    def format_topology_banner(self) -> str:
        """Creates a compact 1-line prompt injection banner for coding agents."""
        god_nodes = self.get_god_nodes(top_n=4)
        if not god_nodes:
            return ""
        items = [f"{g['file']} ({g['dependents']} deps)" for g in god_nodes]
        return f"[TokenSafe Architecture Map | Core Hubs: {', '.join(items)}]"


class GraphEngine:
    """Multi-Project Manager for automatic background graph execution."""

    _instances: Dict[str, CodeGraph] = {}

    @classmethod
    def find_project_root(cls, start_path: str) -> str:
        """Walks up from start_path to locate project boundary markers."""
        curr = os.path.abspath(start_path)
        if os.path.isfile(curr):
            curr = os.path.dirname(curr)

        root = os.path.abspath(os.sep)
        while curr and curr != root:
            for marker in (".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "requirements.txt"):
                if os.path.exists(os.path.join(curr, marker)):
                    return curr
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent
        return os.path.abspath(start_path if os.path.isdir(start_path) else os.path.dirname(start_path))

    @classmethod
    def get_instance(cls, root_dir: str = ".") -> CodeGraph:
        root_abs = os.path.abspath(root_dir)
        # Rescans are full AST crawls; a 3s TTL caused near-constant rescans on every
        # smart_read_file(symbol=...) call. 60s keeps graphs fresh without the churn.
        rescan_ttl = float(os.environ.get("TOKENSAFE_GRAPH_TTL", "60.0"))
        if root_abs not in cls._instances:
            graph = CodeGraph(root_abs, use_disk_cache=True)
            graph.scan()
            cls._instances[root_abs] = graph
        else:
            graph = cls._instances[root_abs]
            if time.time() - graph.last_scanned > rescan_ttl:
                graph.scan()
        return graph

    @classmethod
    def for_file(cls, file_path: str) -> Tuple[CodeGraph, str]:
        """Resolves the owning project graph and relative file path for any arbitrary file."""
        root = cls.find_project_root(file_path)
        graph = cls.get_instance(root)
        rel_path = os.path.relpath(os.path.abspath(file_path), root).replace("\\", "/")
        return graph, rel_path

def main():
    import sys
    args = sys.argv[1:]
    cmd = args[0] if args else "graph"

    target_dir = "."
    graph = GraphEngine.get_instance(target_dir)

    if cmd in ("graph", "nodes", "god-nodes"):
        print(f"\n🔍 TokenSafe Graph Engine — Knowledge Graph & Centrality Analysis")
        print(f"   Indexed: {len(graph.file_symbols)} code files, {len(graph.symbols)} symbols\n")
        god_nodes = graph.get_god_nodes(top_n=7)
        print("⚡ Architectural Hubs (God Nodes):")
        for idx, g in enumerate(god_nodes, 1):
            print(f"  {idx}. {g['file']}")
            print(f"     Degree Centrality: {g['centrality']} | Dependents: {g['dependents']} | Dependencies: {g['dependencies']} | Symbols: {g['symbols_count']}")
        print(f"\n💡 Tip: Run 'tokensafe map' to generate tokensafe-out/API_SURFACE.md\n")

    elif cmd in ("map", "surface"):
        out_file = os.path.join(target_dir, "tokensafe-out", "API_SURFACE.md")
        graph.generate_api_surface(out_file)
        print(f"\n✓ Generated API Surface Map: {out_file} ({os.path.getsize(out_file):,} bytes)")
        print(f"  AI agents can read this file in ~1.5k tokens instead of reading the entire repo.\n")

    elif cmd == "skeleton":
        if len(args) < 2:
            print("Usage: tokensafe skeleton <file_path> [target_symbol]")
            sys.exit(1)
        file_path = args[1]
        target_symbol = args[2] if len(args) > 2 else None
        res = graph.graph_guided_skeletonize(file_path, target_symbol)
        print(res)

    else:
        print(f"Unknown command: {cmd}. Available: graph, map, skeleton")

if __name__ == "__main__":
    main()

