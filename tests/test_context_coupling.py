"""Context Budget HUD <-> smart compact coupling tests.

Covers the v2.4.2-era coupling fixes:
- agents are TOLD about context_budget / compact_conversation (directives + rules)
- ContextBudget.estimate counts REAL transcript history mass
- HistoryCompactor.measure_transcript lightweight probe
- claude_code_hook folds history automatically (parity with Antigravity)
"""
import unittest
import os
import json
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeTracker:
    """Minimal tracker double: estimate() must tolerate storage failures and
    still add the real history mass to the breakdown."""

    def _get_connection(self):
        raise RuntimeError("storage unavailable in test")


class TestContextCoupling(unittest.TestCase):

    def test_directives_mention_hud_and_compact(self):
        from src.adapter_core import TOKENSAFE_DIRECTIVES
        self.assertIn("context_budget", TOKENSAFE_DIRECTIVES)
        self.assertIn("compact_conversation", TOKENSAFE_DIRECTIVES)

    def test_rules_mention_hud_and_compact(self):
        with open(os.path.join(PROJECT_ROOT, "rules", "AGENTS.md"), encoding="utf-8") as f:
            rules = f.read()
        self.assertIn("context_budget", rules)
        self.assertIn("compact_conversation", rules)

    def test_measure_transcript_counts_content(self):
        from src.history_compactor import HistoryCompactor
        from src.tokenizer import count_tokens
        path = os.path.join(tempfile.mkdtemp(prefix="ts_coupling_"), "t.jsonl")
        steps = [{"content": "lorem ipsum dolor sit amet " * 40} for _ in range(3)]
        with open(path, "w", encoding="utf-8") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")
        expected = sum(count_tokens("lorem ipsum dolor sit amet " * 40) for _ in steps)
        measured = HistoryCompactor.measure_transcript(path)
        self.assertGreater(measured, 0)
        self.assertEqual(measured, expected)
        self.assertEqual(HistoryCompactor.measure_transcript(os.path.join(PROJECT_ROOT, "no", "such.jsonl")), 0)

    def test_fold_transcript_claude_code_format(self):
        """Regression: Claude Code transcripts use type/message.content blocks
        (tool_use / tool_result), NOT the Antigravity content/tool_calls shape.
        The fold must read REAL mass and file names from the CC format."""
        from src.history_compactor import HistoryCompactor
        path = os.path.join(tempfile.mkdtemp(prefix="ts_cc_fmt_"), "transcript.jsonl")
        big = "alpha beta gamma delta epsilon " * 150  # > 1000 tokens across steps
        lines = [
            json.dumps({"type": "queue-operation", "operation": "enqueue"}),  # noise line
            json.dumps({"type": "attachment", "attachment": {"type": "hook_success"}}),  # noise line
            json.dumps({"type": "user", "message": {"role": "user", "content": f"Fix the wheel in {big}"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "locate it first", "text": "thinking " + big},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "src/views/Wheel.tsx"}},
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file body"},
            ]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "src/views/Wheel.tsx", "old_string": "a", "new_string": "b"}},
                {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "npm test -- --run"}},
            ]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": big},
            ]}}),
        ]
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")

        measured = HistoryCompactor.measure_transcript(path)
        self.assertGreater(measured, 1000, "CC-format transcript mass must be counted")

        ledger, orig_t, folded_t, stats = HistoryCompactor.fold_transcript(path, 6)
        self.assertIsNotNone(ledger, "CC-format transcript must fold")
        self.assertIn("Wheel.tsx", ledger)
        self.assertIn("npm", ledger)
        self.assertGreater(orig_t, 1000)
        self.assertGreater(stats["saved_tokens"], 0)

    def test_fold_transcript_antigravity_format_still_works(self):
        from src.history_compactor import HistoryCompactor
        path = os.path.join(tempfile.mkdtemp(prefix="ts_ag_fmt_"), "transcript.jsonl")
        big = "alpha beta gamma delta epsilon " * 150
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"content": big, "tool_calls": [
                {"name": "view_file", "args": {"Path": "src/wallet.ts"}},
                {"name": "run_command", "args": {"CommandLine": "pytest -q"}},
            ]}) + "\n")
            for _ in range(3):  # fold gate requires >= 4 steps
                f.write(json.dumps({"content": big}) + "\n")
        ledger, orig_t, _, stats = HistoryCompactor.fold_transcript(path, 3)
        self.assertIsNotNone(ledger)
        self.assertIn("wallet.ts", ledger)
        self.assertIn("pytest", ledger)

    def test_estimate_includes_history_mass(self):
        from src.governor import ContextBudget
        budget = ContextBudget.estimate(
            _FakeTracker(), session_id="coupling_test",
            model="claude-x", history_tokens=120_000
        )
        self.assertEqual(budget["breakdown"]["history"], 120_000)
        self.assertGreaterEqual(budget["used_pct"], 60.0)
        self.assertIn("Semi-real", budget["caveat"])

    def test_estimate_without_history_keeps_floor_caveat(self):
        from src.governor import ContextBudget
        budget = ContextBudget.estimate(_FakeTracker(), session_id="coupling_test", model="claude-x")
        self.assertIn("Floor estimate", budget["caveat"])

    def _load_hook(self):
        """hooks/ ships without __init__.py — load the adapter module by path
        (it self-registers PROJECT_ROOT on sys.path for `src.*` imports)."""
        import importlib.util
        import sys
        hook_path = os.path.join(PROJECT_ROOT, "hooks", "claude_code_hook.py")
        spec = importlib.util.spec_from_file_location("claude_code_hook_under_test", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_claude_code_hook_folds_history(self):
        from unittest.mock import patch
        claude_code_hook = self._load_hook()

        tmpdir = tempfile.mkdtemp(prefix="ts_hookfold_")
        path = os.path.join(tmpdir, "transcript.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(6):
                f.write(json.dumps({"content": "alpha beta gamma delta epsilon " * 200}) + "\n")

        recorded = []

        class _StubTracker:
            def record_event(self, **kwargs):
                recorded.append(kwargs)

            def get_last_event(self, *args, **kwargs):
                return None

        payload = {
            "session_id": "hookfold_test",
            "prompt": "hi",
            "transcript_path": path,
        }
        with patch("src.metrics.MetricsTracker", _StubTracker):
            result = claude_code_hook.handle("UserPromptSubmit", payload)

        ctx = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[TOCP State Ledger]", ctx)
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["event_type"], "HISTORY_FOLD")

    def test_claude_code_hook_sessionstart_runs_updater(self):
        from unittest.mock import patch
        claude_code_hook = self._load_hook()

        with patch("src.version_checker.VersionChecker.notify_os_update_background") as mock_bg:
            result = claude_code_hook.handle("SessionStart", {"session_id": "s1"})
            self.assertTrue(mock_bg.called)
            self.assertIn("additionalContext", result["hookSpecificOutput"])


if __name__ == "__main__":
    unittest.main()
