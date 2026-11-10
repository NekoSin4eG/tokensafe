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
        res = ScopedSearch.search("HARDCODED_VERSION", scope="code")
        self.assertTrue(res["success"])
        self.assertGreater(res["total_matches"], 0)
        self.assertTrue(any("src/cloud_backend.py" in m["file"] for m in res["matches"]))
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
        needle = "hardcoded" + "_version"
        res_lower = ScopedSearch.search(needle, scope="code", case_sensitive=True)
        self.assertEqual(res_lower["total_matches"], 0)

        res_upper = ScopedSearch.search("HARDCODED_VERSION", scope="code", case_sensitive=True)
        self.assertGreater(res_upper["total_matches"], 0)

    def test_mcp_scoped_search_integration(self):
        res = handle_tool_call("scoped_search", {"query": "HARDCODED_VERSION", "scope": "code"})
        self.assertNotIn("isError", res)
        self.assertIn("content", res)
        text = res["content"][0]["text"]
        self.assertIn("TokenSafe Scoped Search", text)
        self.assertIn("src/cloud_backend.py", text)


if __name__ == "__main__":
    unittest.main()
