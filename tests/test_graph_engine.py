"""
Unit tests for TokenSafe GraphEngine: Dependency Analysis & Graph-Guided Skeletonization.
"""

import unittest
import os
import shutil
import tempfile
from src.graph_engine import CodeGraph, GraphEngine

class TestGraphEngine(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tokensafe_graph_test_")

        # Create module A (God Node with core utilities)
        self.file_a = os.path.join(self.test_dir, "core.py")
        with open(self.file_a, "w") as f:
            f.write('''"""Core system module."""
import os

class CoreManager:
    def __init__(self):
        self.state = "ready"

    def execute_transaction(self, tx_id: str):
        """Processes transaction with logging."""
        self._log_audit(tx_id)
        return True

    def _log_audit(self, msg: str):
        print(f"audit: {msg}")

    def unused_helper(self):
        """Unused helper function."""
        print("unused")
''')

        # Create module B (Consumer)
        self.file_b = os.path.join(self.test_dir, "service.py")
        with open(self.file_b, "w") as f:
            f.write('''"""Service layer."""
from core import CoreManager

def run_service():
    mgr = CoreManager()
    mgr.execute_transaction("tx_123")
''')

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_graph_scan_and_indexing(self):
        graph = CodeGraph(self.test_dir)
        count = graph.scan()
        self.assertEqual(count, 2)
        self.assertIn("core.py", graph.file_symbols)
        self.assertIn("service.py", graph.file_symbols)

        # Check detected symbols
        core_syms = [s.split("::")[-1] for s in graph.file_symbols["core.py"]]
        self.assertIn("CoreManager", core_syms)
        self.assertIn("execute_transaction", core_syms)
        self.assertIn("_log_audit", core_syms)

    def test_god_nodes_ranking(self):
        graph = CodeGraph(self.test_dir)
        graph.scan()
        god_nodes = graph.get_god_nodes(top_n=2)
        self.assertTrue(len(god_nodes) >= 1)
        # core.py has incoming calls/imports so it should rank first
        self.assertEqual(god_nodes[0]["file"], "core.py")

    def test_graph_guided_skeletonization(self):
        graph = CodeGraph(self.test_dir)
        graph.scan()

        # Target: execute_transaction
        # Expected:
        # - execute_transaction: FULL BODY
        # - _log_audit (1-hop callee): FULL BODY
        # - unused_helper: SKELETONIZED (...)
        # - __init__: SKELETONIZED (...)
        output = graph.graph_guided_skeletonize(self.file_a, target_symbol="execute_transaction")

        self.assertIn("def execute_transaction", output)
        self.assertIn("self._log_audit(tx_id)", output)  # Target retains full body
        self.assertIn("def _log_audit", output)
        self.assertIn("audit:", output)                  # 1-hop callee retains full body
        self.assertIn("def unused_helper", output)
        unused_def = output.split("def unused_helper")[1]
        self.assertNotIn("print(", unused_def)           # print("unused") was stripped, only docstring + ellipsis remain

    def test_api_surface_generation(self):
        graph = CodeGraph(self.test_dir)
        graph.scan()
        out_file = os.path.join(self.test_dir, "API_SURFACE.md")
        content = graph.generate_api_surface(output_path=out_file)

        self.assertTrue(os.path.exists(out_file))
        self.assertIn("# TokenSafe Architecture & API Surface", content)
        self.assertIn("CoreManager", content)
        self.assertIn("execute_transaction", content)

    def test_incremental_scan_skips_unchanged(self):
        graph = CodeGraph(self.test_dir)
        reindexed_1 = graph.scan()
        self.assertEqual(reindexed_1, 2)

        # Second scan with no file changes
        reindexed_2 = graph.scan()
        self.assertEqual(reindexed_2, 0)

    def test_topology_banner(self):
        graph = CodeGraph(self.test_dir)
        graph.scan()
        banner = graph.format_topology_banner()
        self.assertIn("[TokenSafe Architecture Map", banner)
        self.assertIn("core.py", banner)

    def test_python_duplicate_method_names_and_class_call_isolation(self):
        dup_file = os.path.join(self.test_dir, "models.py")
        with open(dup_file, "w") as f:
            f.write('''class User:
    def save(self):
        persist_user()

class Order:
    def save(self):
        persist_order()
''')
        graph = CodeGraph(self.test_dir)
        graph.scan()
        syms = graph.file_symbols["models.py"]
        self.assertIn("models.py::User::save", syms)
        self.assertIn("models.py::Order::save", syms)
        self.assertEqual(len(syms), len(set(syms)))
        # Calls inside methods must NOT leak onto the ClassDef node
        self.assertNotIn("call:persist_user", graph.edges.get("models.py::User", set()))
        self.assertIn("call:persist_user", graph.edges.get("models.py::User::save", set()))

    def test_braces_go_imports_ts_methods_and_no_const_false_positives(self):
        ts_file = os.path.join(self.test_dir, "handler.ts")
        with open(ts_file, "w") as f:
            f.write('''import { helper } from "./util";
const MAX_RETRIES = 3;
export class Handler {
  async process(id: string) {
    return helper(id);
  }
}
''')
        util_file = os.path.join(self.test_dir, "util.ts")
        with open(util_file, "w") as f:
            f.write('''export const helper = (id: string) => {
  return id.trim();
};
''')
        go_file = os.path.join(self.test_dir, "main.go")
        with open(go_file, "w") as f:
            f.write('''package main
import (
  "fmt"
  "os"
)
func run() {
  fmt.Println(os.Args)
}
''')
        for env_val in ("0", "auto"):
            prev = os.environ.get("TOKENSAFE_TREESITTER")
            os.environ["TOKENSAFE_TREESITTER"] = env_val
            try:
                graph = CodeGraph(self.test_dir)
                graph.scan()
                ts_syms = [s.split("::")[-1] for s in graph.file_symbols["handler.ts"]]
                self.assertNotIn("MAX_RETRIES", ts_syms)
                self.assertIn("Handler", ts_syms)
                self.assertIn("process", ts_syms)
                # Resolved TS relative import edge
                self.assertIn("file:util.ts", graph.edges.get("file:handler.ts", set()))
                # Go block import parsed
                self.assertIn("mod:fmt", graph.edges.get("file:main.go", set()))
                self.assertIn("mod:os", graph.edges.get("file:main.go", set()))
            finally:
                if prev is None:
                    os.environ.pop("TOKENSAFE_TREESITTER", None)
                else:
                    os.environ["TOKENSAFE_TREESITTER"] = prev

    def test_polyglot_extensions_and_two_hop_neighborhood(self):
        files = {
            "lib.rs": "pub struct Repo {}\nimpl Repo {\n  pub fn fetch(&self) { db_read(); }\n}\n",
            "App.java": "public class App { public void start() { init(); } }\n",
            "Main.kt": "class Main { private suspend fun run() { ping() } }\n",
            "Script.kts": "fun boot() { setup() }\n",
            "View.swift": "class View { public func render() { draw() } }\n",
            "Service.cs": "public class Service { public void Exec() { Run(); } }\n",
            "ctrl.php": "<?php\nclass Ctrl { public function index() { view(); } }\n",
            "engine.cpp": "class Engine { public: void tick() { step(); } };\n",
        }
        for name, body in files.items():
            with open(os.path.join(self.test_dir, name), "w") as f:
                f.write(body)

        graph = CodeGraph(self.test_dir)
        graph.scan()
        for name in files:
            self.assertIn(name, graph.file_symbols, f"{name} should be indexed by GraphEngine")
            self.assertTrue(len(graph.file_symbols[name]) >= 1, f"{name} should expose at least 1 symbol")

        # 2-hop neighborhood test
        hop_file = os.path.join(self.test_dir, "chain.py")
        with open(hop_file, "w") as f:
            f.write("def a(): b()\ndef b(): c()\ndef c(): pass\n")
        graph.scan()
        nb = graph.get_neighborhood("a", target_file="chain.py", include_two_hop=True)
        self.assertIn("chain.py::b", nb["one_hop"])
        self.assertIn("chain.py::c", nb["two_hop"])

    def test_god_nodes_downweight_tests_and_cli_commands(self):
        tests_dir = os.path.join(self.test_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        with open(os.path.join(tests_dir, "test_heavy.py"), "w") as f:
            f.write("from core import CoreManager\ndef test_one():\n    CoreManager().execute_transaction('1')\n")
            for i in range(15):
                f.write(f"def helper_{i}(): pass\n")

        graph = CodeGraph(self.test_dir)
        graph.scan()
        god_nodes = graph.get_god_nodes(top_n=3)
        self.assertEqual(god_nodes[0]["file"], "core.py")

        # Verify CLI graph / map / skeleton do not crash with TypeError
        import io
        import contextlib
        import sys as _sys
        from src.cli import main as cli_main

        for argv in (
            ["tokensafe", "graph", self.test_dir],
            ["tokensafe", "map", self.test_dir],
            ["tokensafe", "skeleton", self.file_a, "execute_transaction"],
        ):
            buf = io.StringIO()
            old_argv = _sys.argv
            try:
                _sys.argv = argv
                with contextlib.redirect_stdout(buf):
                    cli_main()
            finally:
                _sys.argv = old_argv
            self.assertTrue(len(buf.getvalue()) > 0)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "tokensafe-out", "API_SURFACE.md")))

    def test_disk_cache_roundtrip_and_size_invalidation(self):
        graph1 = CodeGraph(self.test_dir, use_disk_cache=True)
        cache_path = graph1._cache_path()
        try:
            self.assertEqual(graph1.scan(), 2)

            graph2 = CodeGraph(self.test_dir, use_disk_cache=True)
            self.assertEqual(graph2.scan(), 0)
            self.assertIn("core.py", graph2.file_symbols)

            # Same-mtime different-size rewrite must still invalidate
            st = os.stat(self.file_a)
            with open(self.file_a, "a") as f:
                f.write("\ndef added_fn():\n    return 1\n")
            os.utime(self.file_a, (st.st_atime, st.st_mtime))
            self.assertEqual(graph2.scan(), 1)
            self.assertIn("core.py::added_fn", graph2.symbols)
        finally:
            if os.path.exists(cache_path):
                os.remove(cache_path)

if __name__ == "__main__":
    unittest.main()
