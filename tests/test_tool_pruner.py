"""
Unit tests for session-stable tool pruning (v3.0.0 proxy stage).
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from src.tool_pruner import apply_subset, detect_prune_miss


def _tool(name: str, desc: str = "A test tool") -> dict:
    return {"name": name, "description": desc, "input_schema": {"type": "object"}}


class TestToolPruner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_pruner_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ.pop("TOKENSAFE_TOOL_PRUNING", None)
        os.environ.pop("TOKENSAFE_TOOL_PRUNE_KEEP", None)
        os.environ.pop("TOKENSAFE_TOOL_PRUNE_ALLOWLIST", None)
        self._state_patch = patch("src.tool_pruner._state_path",
                                  return_value=os.path.join(self._tmp, "state.json"))
        self._state_patch.start()
        self.addCleanup(self._state_patch.stop)

    def _tools(self, n: int = 30) -> list:
        return [_tool(f"mcp_server_{i}_tool", f"Handles domain {i}: parse, render, sync widget-{i}") for i in range(n)]

    def _messages(self, text: str = "Fix the login widget and sync user sessions") -> list:
        return [{"role": "user", "content": text}]

    def test_flag_off_is_noop(self):
        tools = self._tools()
        out, meta = apply_subset(tools, self._messages())
        self.assertIs(out, tools)
        self.assertFalse(meta["applied"])

    def test_small_pool_is_noop(self):
        tools = self._tools(5)
        with patch.dict(os.environ, {"TOKENSAFE_TOOL_PRUNING": "1"}):
            out, meta = apply_subset(tools, self._messages())
        self.assertIs(out, tools)
        self.assertFalse(meta["applied"])

    def test_subset_applied_and_stable_per_session(self):
        tools = self._tools(30)
        msgs = self._messages("Fix the login widget-3 and sync widget-7 sessions")
        with patch.dict(os.environ, {"TOKENSAFE_TOOL_PRUNING": "1", "TOKENSAFE_TOOL_PRUNE_KEEP": "10"}):
            out1, meta1 = apply_subset(tools, msgs)
            out2, meta2 = apply_subset(tools, msgs)
        self.assertTrue(meta1["applied"])
        self.assertEqual(len(out1), 10)
        self.assertGreater(meta1["saved_tokens"], 0)
        # Session-stable: same subset served from state on the second call
        self.assertEqual([t["name"] for t in out1], [t["name"] for t in out2])
        self.assertTrue(meta2.get("cached"))

    def test_allowlist_tool_always_included(self):
        tools = self._tools(30) + [_tool("zz_critical_tool", "must always be available")]
        msgs = self._messages("unrelated task about transforms")
        with patch.dict(os.environ, {
            "TOKENSAFE_TOOL_PRUNING": "1",
            "TOKENSAFE_TOOL_PRUNE_KEEP": "4",
            "TOKENSAFE_TOOL_PRUNE_ALLOWLIST": "zz_critical_tool",
        }):
            out, meta = apply_subset(tools, msgs)
        names = [t["name"] for t in out]
        self.assertIn("zz_critical_tool", names)
        self.assertEqual(len(names), 5, "4 scored + 1 allowlisted beyond K")

    def test_detect_prune_miss(self):
        sent = {"tool_a", "tool_b"}
        resp = {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "tool_a", "arguments": "{}"}},
            {"function": {"name": "pruned_tool_x", "arguments": "{}"}},
        ]}}]}
        self.assertEqual(detect_prune_miss(resp, sent), "pruned_tool_x")
        self.assertIsNone(detect_prune_miss({"choices": [{"message": {"content": "no tools"}}]}, sent))
        self.assertIsNone(detect_prune_miss({}, sent))


if __name__ == "__main__":
    unittest.main()
