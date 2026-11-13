"""
Unit tests for the egress-economy write guard (v3.0.0).
"""
import os
import tempfile
import unittest

from src.adapter_core import guard_write


class TestGuardWrite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_gw_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        self.path = os.path.join(self._tmp, "app.py")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(f"def existing_fn_{i}():\n    return {i}" for i in range(30)))

    def test_small_delta_asks_with_edit_hint(self):
        new_content = "\n".join(f"def existing_fn_{i}():\n    return {i}" for i in range(28))
        new_content += "\n\ndef brand_new_fn():\n    return 42"
        verdict = guard_write(self.path, new_content, agent="claude_code", session_id="gw1")
        self.assertEqual(verdict["decision"], "ask")
        self.assertIn("Edit", verdict["reason"])
        self.assertGreater(verdict["saved_tokens"], 0)

    def test_wholesale_rewrite_of_large_file_denies(self):
        new_content = "\n".join(f"# totally different line {i}" for i in range(60))
        verdict = guard_write(self.path, new_content, agent="claude_code", session_id="gw1")
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("Edit", verdict["reason"])

    def test_new_file_allows(self):
        verdict = guard_write(os.path.join(self._tmp, "brand_new.py"), "print('hi')\n", agent="claude_code")
        self.assertEqual(verdict["decision"], "allow")

    def test_append_allows(self):
        verdict = guard_write(self.path, "more", append=True, agent="claude_code")
        self.assertEqual(verdict["decision"], "allow")

    def test_small_existing_file_allows(self):
        small = os.path.join(self._tmp, "small.py")
        with open(small, "w", encoding="utf-8") as f:
            f.write("a = 1\nb = 2\n")
        verdict = guard_write(small, "a = 2\nb = 3\nc = 4\n", agent="claude_code")
        self.assertEqual(verdict["decision"], "allow")

    def test_empty_content_allows(self):
        self.assertEqual(guard_write(self.path, "", agent="claude_code")["decision"], "allow")
        self.assertEqual(guard_write("", "content", agent="claude_code")["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
