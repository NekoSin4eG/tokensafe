"""
TokenSafe Graph Engine: Dependency-Aware Graph Analysis & Skeletonization.
Builds a zero-LLM deterministic code graph (AST-based) for Python, TypeScript, and Go.
Powers:
1. Graph-Guided Skeletonization (target + 1-hop full, 2-hop signature, 0-hop pruned).
2. God Nodes Detection (architectural hubs via Degree Centrality).
3. API Surface Map Generation (tokensafe-out/API_SURFACE.md).
4. Subtree Cache Invalidation & Instant Retrieval via incremental mtime tracking.
"""

import os
import ast
import re
import json
import stat
import time
from typing import Dict, Set, List, Optional, Tuple, Any

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "venv", ".venv",
    "__pycache__", ".idea", ".vscode", "data", "coverage", ".pytest_cache",
    "Library", "Applications", "Pictures", "Movies", "Music", "Downloads",
    ".cache", ".npm", ".bun", ".rustup", ".cargo", ".local", ".system_generated"
}

SUPPORTED_EXTS = {".py", ".ts", ".js", ".tsx", ".jsx", ".go"}

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

class CodeGraph:
    """
    Deterministic In-Memory Knowledge Graph of a software repository.
    Nodes: File and Symbol identifiers.
    Edges: Directed dependencies (CALLS, IMPORTS, CONTAINS).
    """

    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        self.symbols: Dict[str, CodeSymbol] = {}
        self.file_symbols: Dict[str, List[str]] = {}  # file_rel_path -> [symbol_keys]
        self.edges: Dict[str, Set[str]] = {}          # caller/importer -> set(callees/imported)
        self.rev_edges: Dict[str, Set[str]] = {}      # callee/imported -> set(callers/importers)
        self.file_mtimes: Dict[str, float] = {}       # file_rel_path -> mtime
        self.last_scanned: float = 0.0

    def add_edge(self, source: str, target: str):
        if not source or not target or source == target:
            return
        if source not in self.edges:
            self.edges[source] = set()
        self.edges[source].add(target)

        if target not in self.rev_edges:
            self.rev_edges[target] = set()
        self.rev_edges[target].add(source)

    def scan(self, force: bool = False) -> int:
        """
        Fast incremental scan of the workspace.
        Parses modified files only; returns number of files re-indexed.
        """
        system_roots = {"/", "/Users", "/home", "/root", os.path.expanduser("~")}
        if self.root_dir in system_roots:
            return 0

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
                        mtime = os.path.getmtime(full_path)
                    except OSError:
                        continue

                    if force or rel_path not in self.file_mtimes or self.file_mtimes[rel_path] < mtime:
                        self._index_file(full_path, rel_path)
                        self.file_mtimes[rel_path] = mtime
                        reindexed += 1

        # Purge removed files
        deleted = set(self.file_mtimes.keys()) - current_files
        for del_path in deleted:
            self._purge_file(del_path)
            self.file_mtimes.pop(del_path, None)

        self.last_scanned = time.time()
        return reindexed

    def _purge_file(self, rel_path: str):
        """Removes symbols and edges belonging to a file."""
        sym_keys = self.file_symbols.pop(rel_path, [])
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
        self.edges.pop(file_node, None)
        self.rev_edges.pop(file_node, None)

    def _index_file(self, full_path: str, rel_path: str):
        """Indexes a single code file using standard AST or regex tokenizers."""
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

        _, ext = os.path.splitext(full_path)
        ext = ext.lower()

        if ext == ".py":
            self._parse_python(content, rel_path, file_node)
        elif ext in (".ts", ".js", ".tsx", ".jsx", ".go"):
            self._parse_braces(content, rel_path, file_node, ext)

    def _parse_python(self, code: str, rel_path: str, file_node: str):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return

        lines = code.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Record imports
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.add_edge(file_node, f"mod:{alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.add_edge(file_node, f"mod:{node.module}")
                    for alias in node.names:
                        self.add_edge(file_node, f"sym:{alias.name}")

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                start = getattr(node, "lineno", 1)
                end = getattr(node, "end_lineno", start)
                sym_key = f"{rel_path}::{node.name}"
                sig = lines[start - 1].strip() if 0 < start <= len(lines) else node.name

                symbol = CodeSymbol(
                    name=node.name,
                    kind=kind,
                    file_path=rel_path,
                    line_start=start,
                    line_end=end,
                    signature=sig
                )
                self.symbols[sym_key] = symbol
                self.file_symbols[rel_path].append(sym_key)
                self.add_edge(file_node, sym_key)

                # Scan function calls inside body
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        callee_name = None
                        if isinstance(sub.func, ast.Name):
                            callee_name = sub.func.id
                        elif isinstance(sub.func, ast.Attribute):
                            callee_name = sub.func.attr
                        if callee_name:
                            self.add_edge(sym_key, f"call:{callee_name}")

    def _parse_braces(self, code: str, rel_path: str, file_node: str, ext: str):
        lines = code.splitlines()

        # Imports
        import_pattern = re.compile(r"""(?:import\s+.*?\s+from\s+['"](.*?)['"]|require\(['"](.*?)['"]\))""")
        for match in import_pattern.finditer(code):
            target_mod = match.group(1) or match.group(2)
            if target_mod:
                self.add_edge(file_node, f"mod:{target_mod}")

        # Functions & Methods
        fn_pattern = re.compile(
            r"""(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\s*\*?\s*([a-zA-Z0-9_$]+)|(?:class|interface|struct|impl|trait)\s+([a-zA-Z0-9_$]+)|func\s+(?:\(.*?\)\s+)?([a-zA-Z0-9_]+)|(?:const|let|var)\s+([a-zA-Z0-9_$]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:React\.(?:memo|forwardRef)\s*\()?(?:useCallback\s*\()?\s*\(?)"""
        )
        call_pattern = re.compile(r"""(?:<|\b)([a-zA-Z0-9_$]{3,})\s*(?:\(|>|(?:\s+[a-zA-Z0-9_$-]+))""")
        current_sym = None

        for idx, line in enumerate(lines, 1):
            match = fn_pattern.search(line)
            if match:
                name = match.group(1) or match.group(2) or match.group(3) or match.group(4)
                if name and name not in {"if", "for", "while", "switch", "catch", "return", "import", "export"}:
                    kind = "class" if any(w in line for w in ("class", "interface", "struct", "impl")) else "function"
                    sym_key = f"{rel_path}::{name}"
                    symbol = CodeSymbol(
                        name=name,
                        kind=kind,
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=line.strip()
                    )
                    self.symbols[sym_key] = symbol
                    self.file_symbols[rel_path].append(sym_key)
                    self.add_edge(file_node, sym_key)
                    current_sym = sym_key
            elif current_sym:
                for cm in call_pattern.finditer(line):
                    callee = cm.group(1)
                    if callee not in {"div", "span", "button", "input", "label", "form", "svg", "path", "true", "false", "null", "undefined"}:
                        self.add_edge(current_sym, f"call:{callee}")

    # -------------------------------------------------------------
    # 1. God Nodes Analysis (Degree Centrality)
    # -------------------------------------------------------------
    def get_god_nodes(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Calculates Degree Centrality across files to locate architectural hubs.
        God Nodes = files with highest incoming (depended upon) and outgoing connections.
        """
        scores: Dict[str, Dict[str, Any]] = {}

        for rel_path in self.file_symbols.keys():
            file_node = f"file:{rel_path}"
            # Outgoing: What this file imports or contains
            out_degree = len(self.edges.get(file_node, set()))
            # Incoming: Who imports or calls this file or its symbols
            in_degree = len(self.rev_edges.get(file_node, set()))

            # Aggregate symbol connections
            for sym_key in self.file_symbols.get(rel_path, []):
                sym_name = sym_key.split("::")[-1]
                call_ref = f"call:{sym_name}"
                in_degree += len(self.rev_edges.get(call_ref, set()))

            total_centrality = in_degree * 2 + out_degree  # Incoming imports weighted heavier
            scores[rel_path] = {
                "file": rel_path,
                "centrality": total_centrality,
                "dependents": in_degree,
                "dependencies": out_degree,
                "symbols_count": len(self.file_symbols.get(rel_path, []))
            }

        ranked = sorted(scores.values(), key=lambda x: x["centrality"], reverse=True)
        return ranked[:top_n]

    # -------------------------------------------------------------
    # 2. Neighborhood & Graph-Guided Skeletonization
    # -------------------------------------------------------------
    def get_neighborhood(self, target_symbol: str) -> Dict[str, Set[str]]:
        """
        Returns 0-hop (target), 1-hop (callees/callers), and 2-hop related symbols.
        """
        target_keys = [k for k in self.symbols if k.endswith(f"::{target_symbol}") or k == target_symbol]
        one_hop = set()

        for tk in target_keys:
            # Direct callees
            for callee in self.edges.get(tk, set()):
                if callee.startswith("call:"):
                    name = callee[5:]
                    for k in self.symbols:
                        if k.endswith(f"::{name}"):
                            one_hop.add(k)
                elif callee in self.symbols:
                    one_hop.add(callee)
            # Direct callers
            for caller in self.rev_edges.get(f"call:{target_symbol}", set()):
                if caller in self.symbols:
                    one_hop.add(caller)

        return {
            "target": set(target_keys),
            "one_hop": one_hop
        }

    def graph_guided_skeletonize(self, full_path: str, target_symbol: Optional[str] = None, include_deps: bool = True) -> str:
        """
        Surgically folds code based on graph connectivity:
        - target_symbol: Retains FULL function body.
        - 1-hop dependencies: Retained if include_deps=True.
        - 2-hop / Unrelated functions in the same file: Stripped to AST signatures (ellipsis).
        Yields up to 90% token reduction without breaking context.
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
            return Skeletonizer.skeletonize_code(code, ext)

        keep_full_names = {target_symbol}
        if include_deps:
            neighborhood = self.get_neighborhood(target_symbol)
            for k in neighborhood.get("one_hop", set()):
                keep_full_names.add(k.split("::")[-1])

        return Skeletonizer.skeletonize_code(code, ext, keep_symbols=keep_full_names)

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
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, "API_SURFACE.md")

        try:
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
            graph = CodeGraph(root_abs)
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

