"""Contract tests for the Antigravity lifecycle hooks (agy-customizations contract):

- PreInvocation reads camelCase `modelName` (hooks.md payload schema)
- One-shot session banners (quota/bloat/directives) moved to SessionStart
- Stop hook: one-shot MAX_TOKEN_BUDGET_EXCEEDED nudge + fullyIdle epilogue
- PreToolUse routes view_file (IDE tool name) through the read pipeline
"""
import os
import sys
import io
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import metrics as metrics_mod
from src.metrics import MetricsTracker


def _fresh_env(tmp: str):
    os.environ["TOKENSAFE_DATA_DIR"] = tmp
    os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
    os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
    metrics_mod._INITIALIZED_METRICS_DBS.clear()


def _event_details(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


class TestPreInvocationModelName(unittest.TestCase):
    def test_camelcase_modelname_used_in_metrics(self):
        from hooks import pre_invocation
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {
                "lastUserInput": "please compress this long-winded prompt for me right now",
                "conversationId": "model_name_sess",
                "invocationNum": 5,
                "modelName": "gemini-test-model-9",
            }
            fake_compaction = {
                "saved_tokens": 42, "reduction_pct": 30, "tier": "REGEX",
                "original_tokens": 140, "compact_tokens": 98,
                "action": "test", "target_file": "", "symbol": "",
                "traits": [], "compact_text": "TOCP",
            }
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO), \
                 patch("src.compactor.PromptCompactor.compact", return_value=fake_compaction):
                pre_invocation.main()

            tracker = MetricsTracker(db_path=os.path.join(tmp, "metrics.db"))
            prompt_events = [e for e in tracker.get_recent_events(limit=20) if e.get("event_type") == "PROMPT"]
            self.assertTrue(prompt_events, "PROMPT event not recorded")
            details = _event_details(prompt_events[0].get("details"))
            self.assertEqual(details.get("model"), "gemini-test-model-9")

    def test_legacy_model_key_still_tolerated(self):
        from hooks import pre_invocation
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {
                "lastUserInput": "please compress this long-winded prompt for me right now",
                "conversationId": "model_name_sess_legacy",
                "invocationNum": 5,
                "model": "legacy-model-name",
            }
            fake_compaction = {
                "saved_tokens": 42, "reduction_pct": 30, "tier": "REGEX",
                "original_tokens": 140, "compact_tokens": 98,
                "action": "test", "target_file": "", "symbol": "",
                "traits": [], "compact_text": "TOCP",
            }
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO), \
                 patch("src.compactor.PromptCompactor.compact", return_value=fake_compaction):
                pre_invocation.main()

            tracker = MetricsTracker(db_path=os.path.join(tmp, "metrics.db"))
            prompt_events = [e for e in tracker.get_recent_events(limit=20) if e.get("event_type") == "PROMPT"]
            self.assertTrue(prompt_events, "PROMPT event not recorded")
            details = _event_details(prompt_events[0].get("details"))
            self.assertEqual(details.get("model"), "legacy-model-name")


class TestPreInvocationLeanTurn(unittest.TestCase):
    def test_first_turn_injects_no_oneshot_banners(self):
        """Quota/bloat banners and full directives live in session_start.py now;
        PreInvocation turn 1 must stay lean."""
        from hooks import pre_invocation
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {"lastUserInput": "x", "conversationId": "lean_sess", "invocationNum": 1}
            with patch("src.license_manager.LicenseManager.check_quota",
                       return_value={"is_quota_exceeded": False, "warning": "QUOTA_BANNER", "tier": "free"}), \
                 patch("src.environment_auditor.EnvironmentAuditor.get_bloat_warning",
                       return_value="BLOAT_BANNER"), \
                 patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO) as out:
                pre_invocation.main()
            output = out.getvalue()
            self.assertNotIn("QUOTA_BANNER", output)
            self.assertNotIn("BLOAT_BANNER", output)
            self.assertNotIn("Mandatory Directives", output)


class TestSessionStartHook(unittest.TestCase):
    def test_injects_directives_and_quota_banner_once(self):
        from hooks import session_start
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {"conversationId": "ss_sess", "workspacePaths": [tmp]}
            with patch("src.license_manager.LicenseManager.check_quota",
                       return_value={"is_quota_exceeded": False, "warning": "QUOTA_BANNER"}), \
                 patch("src.version_checker.VersionChecker.notify_os_update_background"), \
                 patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO) as out:
                session_start.main()
            data = json.loads(out.getvalue())
            steps = data["injectSteps"]
            texts = [s["systemMessage"]["systemMessage"] for s in steps if "systemMessage" in s]
            markdowns = " ".join(
                s["systemMessage"].get("metadata", {}).get("user_markdown", "")
                for s in steps if "systemMessage" in s
            )
            self.assertTrue(any("Mandatory Directives" in t for t in texts))
            self.assertIn("QUOTA_BANNER", markdowns)

    def test_workspace_paths_drive_bloat_audit(self):
        from hooks import session_start
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {"conversationId": "ss_ws", "workspacePaths": [tmp]}
            with patch("src.license_manager.LicenseManager.check_quota",
                       return_value={"is_quota_exceeded": False}), \
                 patch("src.version_checker.VersionChecker.notify_os_update_background"), \
                 patch("src.environment_auditor.EnvironmentAuditor.get_bloat_warning",
                       return_value="BLOAT_BANNER") as bloat_mock, \
                 patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO):
                session_start.main()
            bloat_mock.assert_called_once_with(workspace_root=tmp)


class TestStopHook(unittest.TestCase):
    def test_budget_nudge_fires_exactly_once(self):
        from hooks import stop_hook
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {
                "conversationId": "stop_sess",
                "terminationReason": "MAX_TOKEN_BUDGET_EXCEEDED",
                "fullyIdle": False,
            }
            outputs = []
            for _ in range(2):
                with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                     patch("sys.stdout", new_callable=io.StringIO) as out:
                    stop_hook.main()
                outputs.append(json.loads(out.getvalue()))
            self.assertEqual(outputs[0].get("decision"), "continue")
            self.assertIn("reason", outputs[0])
            self.assertEqual(outputs[1], {})

            tracker = MetricsTracker(db_path=os.path.join(tmp, "metrics.db"))
            nudges = [e for e in tracker.get_recent_events(limit=20) if e.get("event_type") == "STOP_BUDGET_NUDGE"]
            self.assertEqual(len(nudges), 1)

    def test_other_reason_allows_stop_but_idle_flushes(self):
        from hooks import stop_hook
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            payload = {"conversationId": "stop_idle", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True}
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO) as out, \
                 patch("src.telemetry_sync.TelemetrySync.sync_background") as sync_mock:
                stop_hook.main()
            self.assertEqual(json.loads(out.getvalue()), {})
            sync_mock.assert_called_once()

    def test_garbage_input_allows_stop(self):
        from hooks import stop_hook
        with patch("sys.stdin", io.StringIO("not json")), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            stop_hook.main()
        self.assertEqual(json.loads(out.getvalue()), {})


class TestViewFileRouting(unittest.TestCase):
    def test_view_file_goes_through_read_pipeline(self):
        from hooks.pre_tool_use import main as hook_main
        with tempfile.TemporaryDirectory() as tmp:
            _fresh_env(tmp)
            with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".py") as f:
                for i in range(300):
                    f.write(f"def fn_{i}():\n    return {i}\n")
                path = f.name

            payload = {
                "toolCall": {"name": "view_file", "args": {"Path": path}},
                "conversationId": "view_file_sess",
            }
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO) as out:
                hook_main()
                res = json.loads(out.getvalue())

            self.assertEqual(res["decision"], "allow")
            tracker = MetricsTracker(db_path=os.path.join(tmp, "metrics.db"))
            types = {e.get("event_type") for e in tracker.get_recent_events(limit=20)}
            self.assertTrue(
                types & {"AST_SKELETON", "TOOL_READ", "GRAPH_SKELETON"},
                f"view_file did not hit the read pipeline; events: {types}",
            )
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
