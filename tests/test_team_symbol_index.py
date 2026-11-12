import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import src.mcp_server as mcp
from src.graph_engine import GraphEngine
from src.mcp_server import _repo_fingerprint, _dispatch_tool_call


class TestTeamSymbolIndex(unittest.TestCase):
    """Shared Team Symbol Index: machine-independent repo fingerprint +
    team-cache reuse for get_architecture_graph / get_api_surface."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = os.path.join(self.dir, "myrepo")
        os.makedirs(self.repo)
        with open(os.path.join(self.repo, "a.py"), "w", encoding="utf-8") as f:
            f.write("class Repo:\n    def fetch(self):\n        return 1\n")
        with open(os.path.join(self.repo, "b.py"), "w", encoding="utf-8") as f:
            f.write("def boot():\n    return Repo()\n")
        self._old_db_env = os.environ.get("TOKENSAFE_DB_PATH")
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self.dir, "metrics.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        if self._old_db_env is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._old_db_env

    def test_fingerprint_stable_and_machine_independent(self):
        # Identical trees under DIFFERENT absolute roots (two clones) must collide
        clone_parent = tempfile.mkdtemp()
        try:
            clone = os.path.join(clone_parent, "myrepo")
            os.makedirs(clone)
            for name in ("a.py", "b.py"):
                shutil.copy(os.path.join(self.repo, name), os.path.join(clone, name))
            g1, _ = GraphEngine.for_file(os.path.join(self.repo, "a.py"))
            g2, _ = GraphEngine.for_file(os.path.join(clone, "a.py"))
            self.assertEqual(_repo_fingerprint(g1), _repo_fingerprint(g2))
        finally:
            shutil.rmtree(clone_parent, ignore_errors=True)
        # A content change (size change) must move the fingerprint
        from src.graph_engine import CodeGraph
        g_pre = CodeGraph(self.repo)
        g_pre.scan()
        fp_pre = _repo_fingerprint(g_pre)
        with open(os.path.join(self.repo, "b.py"), "a", encoding="utf-8") as f:
            f.write("# extra comment line\n")
        g_post = CodeGraph(self.repo)
        g_post.scan()
        fp_post = _repo_fingerprint(g_post)
        self.assertNotEqual(fp_pre, fp_post)

    def test_arch_graph_hit_reuses_team_cache(self):
        cached_payload = json.dumps({
            "project_root": "/teammates/clone",
            "banner": "cached banner",
            "total_files": 2,
            "total_symbols": 2,
            "god_nodes": [{"name": "Repo", "score": 1.0}],
        })
        with patch.object(mcp, "TeamCache") as fake_tc:
            fake_tc.get.return_value = cached_payload
            res = _dispatch_tool_call("get_architecture_graph", {"project_path": self.repo})
        text = res["content"][0]["text"]
        self.assertIn("team_symbol_index", text)
        self.assertIn("cached banner", text)
        fake_tc.put.assert_not_called()

    def test_arch_graph_miss_computes_and_shares(self):
        with patch.object(mcp, "TeamCache") as fake_tc:
            fake_tc.get.return_value = None
            res = _dispatch_tool_call("get_architecture_graph", {"project_path": self.repo})
        text = res["content"][0]["text"]
        self.assertIn("god_nodes", text)
        self.assertNotIn("team_symbol_index", text)
        self.assertEqual(fake_tc.put.call_count, 1)
        args, _ = fake_tc.put.call_args
        self.assertEqual(args[0], "arch_index")
        self.assertIn("god_nodes", json.loads(args[2]))

    def test_api_surface_hit_and_miss(self):
        with patch.object(mcp, "TeamCache") as fake_tc:
            fake_tc.get.return_value = "# cached surface\n"
            res = _dispatch_tool_call("get_api_surface", {"project_path": self.repo})
            self.assertIn("cached surface", res["content"][0]["text"])
            self.assertEqual(fake_tc.put.call_count, 0)
        with patch.object(mcp, "TeamCache") as fake_tc:
            fake_tc.get.return_value = None
            res = _dispatch_tool_call("get_api_surface", {"project_path": self.repo})
            self.assertNotIn("cached surface", res["content"][0]["text"])
            self.assertEqual(fake_tc.put.call_count, 1)
            args, _ = fake_tc.put.call_args
            self.assertEqual(args[0], "arch_index")
            self.assertTrue(args[1].endswith(":surface"))


if __name__ == "__main__":
    unittest.main()
