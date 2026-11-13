"""
Unit tests for the Tool Result Ledger (v3.0.0 proxy stage).
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.result_ledger import FOLD_MARKER, fold_tool_results


class TestResultLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_ledger_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        os.environ.pop("TOKENSAFE_TOOL_LEDGER", None)
        os.environ.pop("TOKENSAFE_HISTORY_DISTILL", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_HISTORY_DISTILL", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        # Keep archives + ledger state inside the tmp sandbox, never ~/.tokensafe
        self._data_patch = patch("src.paths.resolve_data_dir", return_value=self._tmp)
        self._data_patch.start()
        self.addCleanup(self._data_patch.stop)

    def _big_code(self, lines: int = 240) -> str:
        return "\n".join(f"def process_payload_{i}(request, response):\n    return validate_and_transform(request, response, mode={i})" for i in range(lines // 2))

    def _messages(self):
        """Two aged big results + one recent big result + one tiny old result."""
        big_code = self._big_code()
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "start the work"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_old_read", "type": "function",
                 "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/app/main.py"})}},
            ]},
            {"role": "tool", "tool_call_id": "call_old_read", "content": big_code},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_old_cmd", "type": "function",
                 "function": {"name": "Bash", "arguments": json.dumps({"command": "pytest -q"})}},
            ]},
            {"role": "tool", "tool_call_id": "call_old_cmd",
             "content": "rootdir: /app\ncollected 5 items\n" +
                        "\n".join(f"tests/test_module_{i}.py::test_case_{i} PASSED in 0.{i:02d}s" for i in range(60)) +
                        "\n==== 5 passed in 0.15s ===="},
            {"role": "assistant", "content": "interim answer"},
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_recent", "type": "function",
                 "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/app/recent.py"})}},
            ]},
            {"role": "tool", "tool_call_id": "call_recent", "content": self._big_code(90)},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "one more tiny check"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_tiny", "type": "function",
                 "function": {"name": "list_dir", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_tiny", "content": "src/\ntests/\nREADME.md"},
        ]

    def test_folds_aged_big_results_and_keeps_recent(self):
        msgs = self._messages()
        out, stats = fold_tool_results(msgs, record_metrics=False)
        self.assertEqual(stats["folded"], 2, "aged read + aged command fold, recent/tiny stay")
        by_id = {m.get("tool_call_id"): m for m in out if m.get("role") == "tool"}
        folded_read = by_id["call_old_read"]["content"]
        self.assertIn(FOLD_MARKER, folded_read)
        self.assertIn("read_file", folded_read)
        self.assertIn("smart_read_file(force_full=true)", folded_read)
        self.assertLess(len(folded_read), len(self._big_code()))
        folded_cmd = by_id["call_old_cmd"]["content"]
        self.assertIn(FOLD_MARKER, folded_cmd)
        self.assertIn("PASS: 5 passed in 0.15s", folded_cmd)
        # Recent big result and tiny old result untouched
        self.assertNotIn(FOLD_MARKER, by_id["call_recent"]["content"])
        self.assertNotIn(FOLD_MARKER, by_id["call_tiny"]["content"])
        # Non-tool messages untouched
        self.assertEqual(out[0]["content"], "sys")

    def test_idempotent_second_pass(self):
        msgs = self._messages()
        out1, s1 = fold_tool_results(msgs, record_metrics=False)
        self.assertEqual(s1["folded"], 2)
        out2, s2 = fold_tool_results(out1, record_metrics=False)
        self.assertEqual(s2["folded"], 0)
        self.assertEqual(out1, out2)

    def test_flag_disabled_is_noop(self):
        msgs = self._messages()
        with patch.dict(os.environ, {"TOKENSAFE_TOOL_LEDGER": "0"}):
            out, stats = fold_tool_results(msgs, record_metrics=False)
        self.assertEqual(stats["folded"], 0)
        self.assertEqual(out, msgs)

    def test_archive_written_and_anchor_points_to_it(self):
        msgs = self._messages()
        with patch("src.paths.resolve_data_dir", return_value=self._tmp):
            out, stats = fold_tool_results(msgs, record_metrics=False)
        self.assertEqual(stats["folded"], 2)
        archive_dir = os.path.join(self._tmp, "tool_ledger")
        archives = os.listdir(archive_dir) if os.path.isdir(archive_dir) else []
        self.assertEqual(len(archives), 2, "both originals archived")
        with open(os.path.join(archive_dir, archives[0]), "r", encoding="utf-8") as f:
            archived = json.load(f)
        self.assertIn("content", archived)
        by_id = {m.get("tool_call_id"): m for m in out if m.get("role") == "tool"}
        self.assertIn("archived", by_id["call_old_read"]["content"])

    def test_metrics_recorded_when_enabled(self):
        msgs = self._messages()
        fold_tool_results(msgs, session_id="ledger_test", record_metrics=True)
        from src.metrics import MetricsTracker
        summary = MetricsTracker().get_summary()
        self.assertEqual(summary["breakdown"].get("TOOL_LEDGER", {}).get("count", 0), 1)
        self.assertGreater(summary["breakdown"]["TOOL_LEDGER"]["saved"], 0)

    def test_generic_prose_head_tail_fold(self):
        prose = "\n".join(f"Log line {i} with some verbose runtime detail." for i in range(160))
        msgs = [
            {"role": "user", "content": "analyze logs"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_command", "arguments": json.dumps({"command": "tail -f app.log"})}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": prose},
        ] + [{"role": "assistant", "content": "x"}] * 8
        out, stats = fold_tool_results(msgs, record_metrics=False)
        self.assertEqual(stats["folded"], 1)
        tool_msg = [m for m in out if m.get("role") == "tool"][0]
        self.assertIn("PASS:", tool_msg["content"], "verbose log folds to head/tail summary")
        self.assertIn(FOLD_MARKER, tool_msg["content"])


if __name__ == "__main__":
    unittest.main()
