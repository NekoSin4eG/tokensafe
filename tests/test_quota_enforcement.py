import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src import license_manager as lm
from src.license_manager import LicenseManager
from src.metrics import MetricsTracker


class TestQuotaCountingAndAntiBypass(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ts_quota_test_")
        self.db_path = os.path.join(self._tmp.name, "metrics.db")
        self._old_db = os.environ.get("TOKENSAFE_DB_PATH")
        self._old_dir = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = self._tmp.name
        os.environ["TOKENSAFE_DB_PATH"] = self.db_path
        self.tracker = MetricsTracker(db_path=self.db_path)

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._old_db
        if self._old_dir is None:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
        else:
            os.environ["TOKENSAFE_DATA_DIR"] = self._old_dir
        try:
            self._tmp.cleanup()
        except Exception:
            try:
                import shutil
                shutil.rmtree(self._tmp.name, ignore_errors=True)
            except Exception:
                pass

    def test_measured_only_quota_excludes_estimated_heuristics(self):
        # Estimated events must NOT burn the Community 500k allowance
        for _ in range(5):
            self.tracker.record_event("s1", "GUARD_JUNK_READ", original_tokens=4000, optimized_tokens=0)
            self.tracker.record_event("s1", "SCOPED_SEARCH", original_tokens=3000, optimized_tokens=300)
            self.tracker.record_event("s1", "HISTORY_FOLD", original_tokens=5000, optimized_tokens=400)
        self.assertEqual(self.tracker.get_monthly_saved_tokens(measured_only=True), 0)
        self.assertGreater(self.tracker.get_monthly_saved_tokens(measured_only=False), 40_000)

        # Measured events DO count towards the 500k allowance
        self.tracker.record_event("s1", "GRAPH_SKELETON", original_tokens=12_000, optimized_tokens=1_000)
        self.tracker.record_event("s1", "SUBAGENT_DEDUP", original_tokens=8_000, optimized_tokens=100)
        self.assertEqual(self.tracker.get_monthly_saved_tokens(measured_only=True), 18_900)

    def test_quota_survives_prune_old_events_and_db_deletion(self):
        self.tracker.record_event("s1", "GRAPH_SKELETON", original_tokens=515_000, optimized_tokens=5_000)
        # Add extra small events then force prune_old_events down to 1 row
        for i in range(10):
            self.tracker.record_event("s1", "AST_SKELETON", original_tokens=200, optimized_tokens=100)
        prune_res = self.tracker.prune(days=0, max_events=1, vacuum=False)
        self.assertGreater(prune_res["pruned_events"], 5)
        self.assertGreaterEqual(self.tracker.get_monthly_saved_tokens(), 510_000)

        # Even if user deletes metrics.db, sidecar .qledger.json preserves trailing 30d watermark
        os.remove(self.db_path)
        from src.metrics import _INITIALIZED_METRICS_DBS
        _INITIALIZED_METRICS_DBS.discard(os.path.abspath(self.db_path))
        fresh_tracker = MetricsTracker(db_path=self.db_path)
        self.assertGreaterEqual(fresh_tracker.get_monthly_saved_tokens(), 510_000)

    def test_server_quota_floor_reconciles_exceeded_state(self):
        self.assertEqual(self.tracker.get_monthly_saved_tokens(), 0)
        self.tracker.record_server_quota_floor(525_000)
        with patch.object(LicenseManager, "get_active_license", return_value=("free", {"tier": "free", "max_tokens_monthly": 500_000})):
            q = LicenseManager.check_quota(self.tracker)
        self.assertTrue(q["is_quota_exceeded"])
        self.assertEqual(q["monthly_saved"], 525_000)
        self.assertEqual(q["remaining_tokens"], 0)

    def test_crypto_lockdown_blocks_ts1_offline_and_env_pubkey_override(self):
        # 1. Test pubkey must not sit in legacy trusted tuple
        self.assertEqual(lm.LEGACY_ED25519_PUBLIC_KEYS, ())

        # 2. Legacy TS1 keys must fail offline validation
        ts1_key = LicenseManager.generate_license("solo", "forged@test.com", version=1, signing_key="any-salt")
        ok_ts1, _, msg_ts1 = LicenseManager.validate_license(ts1_key)
        self.assertFalse(ok_ts1)
        self.assertIn("TS1", msg_ts1)

        # 3. Runtime TOKENSAFE_PUBLIC_KEY env var must be ignored when validating against embedded prod pubkey
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        priv_bytes = bytes.fromhex("22" * 32)
        attacker_pub = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes).public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        ).hex()
        forged_ts2 = LicenseManager.generate_license("team", "attacker@evil.com", signing_key=priv_bytes.hex())

        with patch.object(lm, "DEFAULT_ED25519_PUBLIC_KEY", lm.EMBEDDED_ED25519_PUBLIC_KEY), \
             patch.dict(os.environ, {"TOKENSAFE_PUBLIC_KEY": attacker_pub}):
            ok_env, _, _ = LicenseManager.validate_license(forged_ts2)
        self.assertFalse(ok_env)


class TestQuotaDegradationAndBanners(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ts_quota_deg_")
        self._old_db = os.environ.get("TOKENSAFE_DB_PATH")
        self._old_dir = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = self._tmp.name
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp.name, "metrics.db")
        self.tracker = MetricsTracker(db_path=os.environ["TOKENSAFE_DB_PATH"])
        # Exceed 500k measured tokens
        self.tracker.record_event("seed", "GRAPH_SKELETON", original_tokens=510_000, optimized_tokens=0)

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._old_db
        if self._old_dir is None:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
        else:
            os.environ["TOKENSAFE_DATA_DIR"] = self._old_dir
        try:
            self._tmp.cleanup()
        except Exception:
            try:
                import shutil
                shutil.rmtree(self._tmp.name, ignore_errors=True)
            except Exception:
                pass

    def test_banner_cadence_emits_on_turn_1_and_every_5_turns(self):
        results = [
            LicenseManager.should_emit_quota_banner(self.tracker, "cadence_sess", every_n_turns=5)
            for _ in range(10)
        ]
        # Turns: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
        self.assertEqual(results, [True, False, False, False, True, False, False, False, False, True])

    def test_mcp_server_degrades_pro_tools_while_keeping_guard_free(self):
        from src import mcp_server

        target = os.path.join(self._tmp.name, "sample.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("def helper():\n    return 1\n\ndef main():\n    return helper()\n")
        transcript = os.path.join(self._tmp.name, "transcript.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            for _ in range(5):
                f.write(json.dumps({"content": "long context message " * 100}) + "\n")

        with patch.object(LicenseManager, "get_active_license", return_value=("free", {"tier": "free", "max_tokens_monthly": 500_000})):
            # Pro tool #1: smart_read_file returns raw content + turn-1 Quota Alert banner
            r1 = mcp_server.handle_tool_call("smart_read_file", {"path": target, "symbol": "main"})
            txt1 = r1["content"][0]["text"]
            self.assertIn("TokenSafe Quota Alert", txt1)
            self.assertIn("def helper():", txt1)
            self.assertNotIn("Graph-Guided Skeleton", txt1)

            # Pro tools #2..4 are gated without repeating banner; #5 re-emits banner
            r2 = mcp_server.handle_tool_call("get_architecture_graph", {"project_path": self._tmp.name})
            self.assertTrue(json.loads(r2["content"][0]["text"]).get("quota_exceeded"))
            r3 = mcp_server.handle_tool_call("compact_conversation", {"transcript_path": transcript, "invocation_num": 5})
            self.assertTrue(json.loads(r3["content"][0]["text"]).get("quota_exceeded"))
            r4 = mcp_server.handle_tool_call("compress_prompt", {"prompt": "Refactor auth module carefully"})
            self.assertTrue(json.loads(r4["content"][0]["text"]).get("quota_exceeded"))
            r5 = mcp_server.handle_tool_call("smart_read_file", {"path": target, "symbol": "main"})
            self.assertIn("TokenSafe Quota Alert", r5["content"][0]["text"])

            # Free tools keep working normally (Security Guard + list_dir)
            sec = mcp_server.handle_tool_call("guard_inspect", {"command": "rm -rf /"})
            self.assertTrue(json.loads(sec["content"][0]["text"]).get("blocked"))
            ls_res = mcp_server.handle_tool_call("list_dir", {"path": self._tmp.name})
            self.assertIn("sample.py", ls_res["content"][0]["text"])

    def test_proxy_pauses_governor_and_normalizer_over_quota_but_keeps_secret_redactor(self):
        from src.proxy import TokenSafeProxyHandler

        captured_payloads = []
        sent_headers = []

        class _FakeResp:
            status = 200
            def read(self):
                return json.dumps({"id": "ok"}).encode("utf-8")
            def getheaders(self):
                return [("Content-Type", "application/json")]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        raw_sys = "Static instruction " * 250 + "\nThe current local time is: 2026-11-12T10:00:00Z"
        fake_openai_key = "sk-proj-" + "A" * 44
        raw_user = f"Hello! Could you please carefully inspect auth.py with API key {fake_openai_key}?"
        body = json.dumps({
            "model": "claude-opus-4-6",
            "messages": [
                {"role": "system", "content": raw_sys},
                {"role": "user", "content": raw_user},
            ],
        }).encode("utf-8")
        handler = TokenSafeProxyHandler.__new__(TokenSafeProxyHandler)
        handler.headers = {"Content-Length": str(len(body)), "Authorization": "Bearer test-key"}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock(side_effect=lambda k, v: sent_headers.append((k, v)))
        handler.end_headers = MagicMock()

        with patch.object(LicenseManager, "get_active_license", return_value=("free", {"tier": "free", "max_tokens_monthly": 500_000})), \
             patch.dict(os.environ, {"TOKENSAFE_UPSTREAM_URL": "https://api.anthropic.com/v1/messages"}), \
             patch("urllib.request.urlopen", side_effect=lambda req, timeout=30.0: (captured_payloads.append(json.loads(req.data.decode("utf-8"))), _FakeResp())[1]):
            handler.handle_chat_completions()

        self.assertIn(("X-TokenSafe-Quota", "exceeded"), sent_headers)
        upstream = captured_payloads[0]
        # Secret redactor STILL ran, while normalizer/compactor stayed bypassed
        self.assertIsInstance(upstream["messages"][0]["content"], str)
        self.assertIn("The current local time is:", upstream["messages"][0]["content"])
        self.assertNotIn("AAAA", upstream["messages"][1]["content"])
        self.assertIn("Hello!", upstream["messages"][1]["content"])

    def test_hooks_pause_optimization_and_show_cta_on_cadence(self):
        from hooks import claude_code_hook, pre_invocation

        with patch.object(LicenseManager, "get_active_license", return_value=("free", {"tier": "free", "max_tokens_monthly": 500_000})):
            ss = claude_code_hook.handle("SessionStart", {"session_id": "cc_q_sess"})
            self.assertIn("Quota Alert", ss["hookSpecificOutput"]["additionalContext"])

            # Turn 1 shows CTA, Turn 2 is silent (no prompt compaction), Turn 5 re-emits
            r1 = claude_code_hook.handle("UserPromptSubmit", {"session_id": "cc_q_sess", "prompt": "Please refactor auth.py"})
            self.assertIn("TokenSafe Quota Alert", r1["hookSpecificOutput"]["additionalContext"])
            self.assertNotIn("TOCP Compressed Intent", r1["hookSpecificOutput"]["additionalContext"])
            r2 = claude_code_hook.handle("UserPromptSubmit", {"session_id": "cc_q_sess", "prompt": "Please refactor auth.py"})
            self.assertEqual(r2, {})
            for _ in range(2):
                claude_code_hook.handle("UserPromptSubmit", {"session_id": "cc_q_sess", "prompt": "hi"})
            r5 = claude_code_hook.handle("UserPromptSubmit", {"session_id": "cc_q_sess", "prompt": "hi"})
            self.assertIn("TokenSafe Quota Alert", r5["hookSpecificOutput"]["additionalContext"])

            # PreInvocation Antigravity hook also pauses optimization and emits CTA
            payload = {"lastUserInput": "Please debug slow SQL query in order.py", "conversationId": "agy_q_sess", "invocationNum": 1}
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                 patch("sys.stdout", new_callable=io.StringIO) as out:
                pre_invocation.main()
            parsed = json.loads(out.getvalue())
            steps_txt = json.dumps(parsed)
            self.assertIn("TokenSafe Quota Alert", steps_txt)
            self.assertNotIn("TOCP Compressed Intent", steps_txt)

            # Security guard in Claude Code PreToolUse stays FREE over 500k
            sec = claude_code_hook.handle("PreToolUse", {"session_id": "cc_q_sess", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
            self.assertEqual(sec["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
