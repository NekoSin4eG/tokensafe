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

if __name__ == "__main__":
    unittest.main()
