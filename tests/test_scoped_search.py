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


if __name__ == "__main__":
    unittest.main()
