"""
Unit tests for TokenSafe Scoped Search Engine and MCP integration.
"""
import unittest
import os
import json
from src.scoped_search import ScopedSearch
from src.mcp_server import handle_tool_call


class TestScopedSearch(unittest.TestCase):

    def test_scoped_search_basic(self):
        res = ScopedSearch.search("USD_PER_TOKEN", scope="code")
        self.assertTrue(res["success"])
        self.assertGreater(res["total_matches"], 0)
        self.assertTrue(any("src/metrics.py" in m["file"] for m in res["matches"]))
        self.assertIn("TokenSafe Scoped Search", res["markdown"])

    def test_scoped_search_empty_query(self):
        res = ScopedSearch.search("")
        self.assertFalse(res["success"])
        self.assertIn("error", res)

    def test_scoped_search_config_scope(self):
        res = ScopedSearch.search("tokensafe", scope="config")
        self.assertTrue(res["success"])
        # Should match package.json or pyproject.toml
        files = [m["file"] for m in res["matches"]]
        self.assertTrue(any("package.json" in f or "pyproject.toml" in f for f in files))

    def test_scoped_search_max_results_capping(self):
        res = ScopedSearch.search("import", scope="code", max_results=5)
        self.assertTrue(res["success"])
        self.assertLessEqual(len(res["matches"]), 5)

    def test_scoped_search_case_sensitive(self):
        needle = "usd" + "_per_token"
        res_lower = ScopedSearch.search(needle, scope="code", case_sensitive=True)
        self.assertEqual(res_lower["total_matches"], 0)

        res_upper = ScopedSearch.search("USD_PER_TOKEN", scope="code", case_sensitive=True)
        self.assertGreater(res_upper["total_matches"], 0)

    def test_mcp_scoped_search_integration(self):
        res = handle_tool_call("scoped_search", {"query": "USD_PER_TOKEN", "scope": "code"})
        self.assertNotIn("isError", res)
        self.assertIn("content", res)
        text = res["content"][0]["text"]
        self.assertIn("TokenSafe Scoped Search", text)
        self.assertIn("src/metrics.py", text)

    def test_scoped_search_path_alias(self):
        src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        res = handle_tool_call("scoped_search", {"query": "ScopedSearch", "path": src_dir, "scope": "code"})
        self.assertNotIn("isError", res)
        text = res["content"][0]["text"]
        self.assertIn("scoped_search.py", text)

    def test_scoped_search_rejects_system_root(self):
        res = ScopedSearch.search("anything", project_path="/")
        self.assertFalse(res["success"])
        self.assertIn("system root", res["markdown"])
        home_res = ScopedSearch.search("anything", project_path=os.path.expanduser("~"))
        self.assertFalse(home_res["success"])

    def test_scoped_search_extension_scope(self):
        res = ScopedSearch.search("class ScopedSearch", scope="py")
        self.assertTrue(res["success"])
        self.assertTrue(any("src/scoped_search.py" in m["file"] for m in res["matches"]))

    def test_mcp_scoped_search_subfolder_in_scope(self):
        # Testing agent passing subfolder in 'scope' (e.g. scope="src")
        res = handle_tool_call("scoped_search", {"query": "ScopedSearch", "scope": "src"})
        self.assertNotIn("isError", res)
        text = res["content"][0]["text"]
        self.assertIn("ScopedSearch", text)

    def test_mcp_scoped_search_relative_path(self):
        # Testing agent passing relative path in 'path'
        res = handle_tool_call("scoped_search", {"query": "ScopedSearch", "path": "src"})
        self.assertNotIn("isError", res)
        text = res["content"][0]["text"]
        self.assertIn("ScopedSearch", text)

    def test_regex_modes_tmp_project(self):
        import shutil
        import tempfile
        root = tempfile.mkdtemp(prefix="tokensafe_ss_regex_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as f:
            f.write("alpha_one = 1\nbeta_two = 2\nid_42 = 42\n")
        # Auto mode (use_regex omitted): alternation '|' enables regex
        res = ScopedSearch.search("alpha_one|beta_two", project_path=root)
        self.assertEqual(res["total_matches"], 2)
        self.assertIn("auto-regex", res["markdown"])
        # Auto mode: backslash character class enables regex
        res_cls = ScopedSearch.search(r"id_\d+", project_path=root)
        self.assertEqual(res_cls["total_matches"], 1)
        self.assertIn("auto-regex", res_cls["markdown"])
        # Explicit false forces literal: pipe string does not exist in code
        res_lit = ScopedSearch.search("alpha_one|beta_two", project_path=root, use_regex=False)
        self.assertEqual(res_lit["total_matches"], 0)
        self.assertNotIn("auto-regex", res_lit["markdown"])
        # Empty auto-regex result hints at literal escape hatch
        res_empty = ScopedSearch.search("gamma_nope|delta_nope", project_path=root)
        self.assertEqual(res_empty["total_matches"], 0)
        self.assertIn("force a literal search", res_empty["markdown"])
        # Explicit true still works
        res_re = ScopedSearch.search(r"alpha_\w+", project_path=root, use_regex=True)
        self.assertEqual(res_re["total_matches"], 1)
        # Malformed auto-detected regex degrades to literal instead of erroring
        res_bad = ScopedSearch.search("alpha_(|", project_path=root)
        self.assertTrue(res_bad["success"])

    def test_mcp_scoped_search_auto_regex_passthrough(self):
        # MCP layer must pass omitted use_regex as None (auto), not coerced False
        res = handle_tool_call("scoped_search", {"query": "USD_PER_TOKEN|reduction_pct", "scope": "code"})
        self.assertNotIn("isError", res)
        self.assertIn("auto-regex", res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
