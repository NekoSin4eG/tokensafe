"""
Regression tests for the 2026-11 comprehensive security & performance audit fixes.
Covers: pre_invocation crash (C1), verify_jwt fail-closed (C2), identity IDOR (C3),
TS1 issuance check (H1), MCP firewall wiring (H2), license validation cache (H3),
CJK tokenizer (H4), sanitizer quoted pipes (H5), distiller cooldown (H6),
metrics autoprune, guard git-status, loop detector locking, skeletonizer templates.
"""

import os
import sys
import json
import time
import base64
import hashlib
import shutil
import subprocess
import tempfile
import threading
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tokenizer import count_tokens
from src.command_sanitizer import CommandSanitizer
from src.loop_detector import LoopDetector
from src.skeletonizer import Skeletonizer


class TestPreInvocationHookCrash(unittest.TestCase):
    """C1: hook crashed with UnboundLocalError when invocation_num >= 3 and no transcript."""

    def _run_hook(self, payload: str) -> subprocess.CompletedProcess:
        tmp = tempfile.mkdtemp(prefix="tokensafe_hook_test_")
        try:
            env = dict(os.environ)
            env["TOKENSAFE_DATA_DIR"] = tmp
            env["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
            env["TOKENSAFE_TELEMETRY_DISABLED"] = "1"  # never touch real cloud from tests
            return subprocess.run(
                [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "pre_invocation.py")],
                input=payload, capture_output=True, text=True, timeout=60, env=env
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_third_invocation_without_transcript_does_not_crash(self):
        payload = json.dumps({
            "lastUserInput": "fix bug in auth.py now",
            "conversationId": "test_hook_regression",
            "invocationNum": 3,
            "transcriptPath": ""
        })
        res = self._run_hook(payload)
        self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
        self.assertNotIn("UnboundLocalError", res.stderr)
        out = json.loads(res.stdout.strip())
        self.assertIsInstance(out, dict)

    def test_third_invocation_with_transcript_produces_valid_json(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_transcript_")
        try:
            big_content = "word " * 400  # ~2000 tokens of history mass
            transcript = os.path.join(tmp, "transcript.jsonl")
            with open(transcript, "w", encoding="utf-8") as f:
                for i in range(5):
                    f.write(json.dumps({"content": f"step {i} " + big_content}) + "\n")
            payload = json.dumps({
                "lastUserInput": "fix bug in auth.py now",
                "conversationId": "test_hook_regression_2",
                "invocationNum": 4,
                "transcriptPath": transcript
            })
            res = self._run_hook(payload)
            self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
            out = json.loads(res.stdout.strip())
            self.assertIsInstance(out, dict)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTokenizerCJK(unittest.TestCase):
    """H4: CJK text used to count as ~1 token because the heuristic regex never matched it."""

    def test_chinese_sentence_has_realistic_token_count(self):
        tokens = count_tokens("请帮我修复这个函数中的错误并添加测试")
        self.assertGreaterEqual(tokens, 15)

    def test_short_cjk_not_one_token(self):
        self.assertGreaterEqual(count_tokens("你好世界"), 6)

    def test_japanese_and_korean_counted(self):
        self.assertGreaterEqual(count_tokens("こんにちは世界"), 4)
        self.assertGreaterEqual(count_tokens("안녕하세요 세계"), 4)

    def test_english_regression(self):
        self.assertGreaterEqual(count_tokens("Hello world this is a test"), 6)


class TestCommandSanitizerQuotedPipes(unittest.TestCase):
    """H5: pipes inside quoted patterns must never split stages."""

    def test_grep_pattern_with_pipe_stays_intact(self):
        new_cmd, saved, _ = CommandSanitizer.sanitize('grep -rn "foo|bar" .')
        self.assertIsNotNone(new_cmd)
        self.assertIn('"foo|bar"', new_cmd)
        # The excludes and the head limiter are appended, pattern untouched
        self.assertIn("--exclude-dir=", new_cmd)
        self.assertIn("head -n 30", new_cmd)

    def test_git_grep_with_pipe_pattern_and_logical_or(self):
        new_cmd, _, _ = CommandSanitizer.sanitize('git grep "a|b" || echo none')
        self.assertIsNotNone(new_cmd)
        self.assertIn('"a|b"', new_cmd)
        self.assertIn("|| echo none", new_cmd)

    def test_plain_pipeline_still_split(self):
        new_cmd, _, _ = CommandSanitizer.sanitize("cat access.log | grep ERROR | wc -l")
        # no recursive grep -> no rewrite expected here, just ensure no exception
        self.assertTrue(new_cmd is None or "grep" in (new_cmd or ""))


class TestLicenseValidationCache(unittest.TestCase):
    """H3: repeated validate_license calls must hit the cache and stay key-scoped."""

    def setUp(self):
        from src import license_manager as lm
        self.lm = lm
        priv_bytes = bytes.fromhex("11" * 32)
        h = hashlib.sha512(priv_bytes).digest()
        a = 2 ** 254 + sum(2 ** i * self.lm._ed_bit(h, i) for i in range(3, 254))
        pub_bytes = self.lm._ed_encodepoint(self.lm._ed_scalarmult(self.lm._ED_B, a))
        self.priv_hex = priv_bytes.hex()
        self.pub_hex = pub_bytes.hex()
        self._old_priv = os.environ.get("TOKENSAFE_PRIVATE_KEY")
        self._old_pub = os.environ.get("TOKENSAFE_PUBLIC_KEY")
        os.environ["TOKENSAFE_PRIVATE_KEY"] = self.priv_hex
        os.environ["TOKENSAFE_PUBLIC_KEY"] = self.pub_hex
        with self.lm.LicenseManager._VALIDATION_CACHE_LOCK:
            self.lm.LicenseManager._VALIDATION_CACHE.clear()

    def tearDown(self):
        for key, val in (("TOKENSAFE_PRIVATE_KEY", self._old_priv), ("TOKENSAFE_PUBLIC_KEY", self._old_pub)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        with self.lm.LicenseManager._VALIDATION_CACHE_LOCK:
            self.lm.LicenseManager._VALIDATION_CACHE.clear()

    def test_second_validation_is_cached(self):
        key = self.lm.LicenseManager.generate_license("solo", "cache@test.com", signing_key=self.priv_hex)
        ok1, _, msg1 = self.lm.LicenseManager.validate_license(key)
        self.assertTrue(ok1)
        self.assertNotIn("cached", msg1)
        ok2, payload2, msg2 = self.lm.LicenseManager.validate_license(key)
        self.assertTrue(ok2)
        self.assertIn("cached", msg2)
        self.assertEqual(payload2["email"], "cache@test.com")

    def test_cache_does_not_leak_across_public_keys(self):
        key = self.lm.LicenseManager.generate_license("solo", "cache2@test.com", signing_key=self.priv_hex)
        ok, _, _ = self.lm.LicenseManager.validate_license(key)
        self.assertTrue(ok)
        ok_other, _, _ = self.lm.LicenseManager.validate_license(key, public_key="ff" * 32)
        self.assertFalse(ok_other)

    def test_expired_license_never_cached(self):
        key = self.lm.LicenseManager.generate_license("solo", "old@test.com", duration_days=-1, signing_key=self.priv_hex)
        ok1, _, msg1 = self.lm.LicenseManager.validate_license(key)
        self.assertFalse(ok1)
        ok2, _, msg2 = self.lm.LicenseManager.validate_license(key)
        self.assertFalse(ok2)
        self.assertNotIn("cached", msg2)


class TestLoopDetectorConcurrency(unittest.TestCase):
    """M10: parallel identical calls must all be counted (state file is lock-guarded)."""

    def test_concurrent_identical_calls_all_counted(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_loop_")
        original = LoopDetector.STATE_FILE
        LoopDetector.STATE_FILE = os.path.join(tmp, "loop_state.json")
        try:
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                LoopDetector.check_loop("conv_test", "read_file", {"Path": "/x"})

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with open(LoopDetector.STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["conv_test"]["repeat_count"], 8)
        finally:
            LoopDetector.STATE_FILE = original
            shutil.rmtree(tmp, ignore_errors=True)


class TestSkeletonizerTemplateLiterals(unittest.TestCase):
    """M14: unbalanced braces inside multi-line template literals must not corrupt folding."""

    CODE = (
        "export function broken() {\n"
        "  const tpl = `\n"
        "    { unbalanced\n"
        "  `;\n"
        "  return tpl;\n"
        "}\n"
        "\n"
        "export function keepMe() {\n"
        "  return 42;\n"
        "}\n"
    )

    def test_template_braces_do_not_break_folding(self):
        out = Skeletonizer.skeletonize_code(self.CODE, ".ts")
        self.assertIn("keepMe", out)             # later function is not swallowed by runaway folding
        self.assertNotIn("unbalanced", out)      # template content folded away
        self.assertNotIn("return tpl", out)      # broken() body folded
        # keep_symbols must still be honored after a template-bearing function
        kept = Skeletonizer.skeletonize_code(self.CODE, ".ts", keep_symbols={"keepMe"})
        self.assertIn("return 42", kept)

    def test_css_token_selector_word_boundary(self):
        css = ".html-widget { color: red; }\n:root { --brand: blue; }\n"
        out = Skeletonizer.skeletonize_code(css, ".css")
        self.assertIn("--brand: blue", out)   # real token selector preserved
        self.assertNotIn("color: red", out)   # .html-widget is NOT a token selector anymore


class TestMetricsAutoprune(unittest.TestCase):
    """M7: retention runs on wall-clock cadence via marker file, not per-instance counters."""

    def test_record_event_creates_prune_marker(self):
        from src.metrics import MetricsTracker
        tmp = tempfile.mkdtemp(prefix="tokensafe_metrics_")
        try:
            db = os.path.join(tmp, "metrics.db")
            tracker = MetricsTracker(db_path=db)
            tracker.record_event("s", "PROMPT", 100, 40)
            marker = os.path.join(tmp, ".metrics_last_prune")
            claim = marker + ".claim"
            self.assertTrue(os.path.exists(marker))
            self.assertFalse(os.path.exists(claim))  # claim released after prune
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDistillerCooldown(unittest.TestCase):
    """H6: provider failure trips a cooldown breaker; success clears it."""

    def test_cooldown_lifecycle(self):
        from src.distiller import UniversalDistiller
        tmp = tempfile.mkdtemp(prefix="tokensafe_distill_")
        old = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = tmp
        try:
            UniversalDistiller._clear_failure()
            self.assertFalse(UniversalDistiller._cooldown_active())
            UniversalDistiller._record_failure()
            self.assertTrue(UniversalDistiller._cooldown_active())
            UniversalDistiller._clear_failure()
            self.assertFalse(UniversalDistiller._cooldown_active())
        finally:
            if old is None:
                os.environ.pop("TOKENSAFE_DATA_DIR", None)
            else:
                os.environ["TOKENSAFE_DATA_DIR"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class TestGuardGitStatusDetection(unittest.TestCase):
    """M9: sensitive file detection delegates to `git status` (exact ignore semantics)."""

    def test_gitignore_semantics_respected(self):
        from src.guard import TokenSafeGuard
        if shutil.which("git") is None:
            self.skipTest("git not available")
        tmp = tempfile.mkdtemp(prefix="tokensafe_guard_")
        try:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True, capture_output=True)
            # Case 1: .env present, NOT ignored -> detected
            with open(os.path.join(tmp, ".env"), "w") as f:
                f.write("SECRET=1\n")
            self.assertEqual(TokenSafeGuard._find_unignored_sensitive_file(tmp), ".env")
            # Case 2: .env ignored via .gitignore -> not detected
            with open(os.path.join(tmp, ".gitignore"), "w") as f:
                f.write(".env\n")
            self.assertIsNone(TokenSafeGuard._find_unignored_sensitive_file(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipIf(
    not os.path.exists(os.path.join(PROJECT_ROOT, "src", "cloud_backend.py")),
    "cloud backend not distributed"
)
class TestCloudAuthRegressions(unittest.TestCase):
    """C2/C3: fail-closed Clerk verification and no identity from caller-supplied emails."""

    def _import_cb(self):
        import src.cloud_backend as cb
        return cb

    def test_forged_clerk_token_rejected_when_crypto_unavailable(self):
        """C2: without cryptography, Clerk tokens must be rejected (previously accepted)."""
        cb = self._import_cb()
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
        payload = {
            "sub": "user_2wXYZ123456",
            "iss": "https://clerk.tokensafe.dev",
            "email": "victim@tokensafe.dev",
            "role": "admin",
            "exp": int(time.time()) + 3600,
        }
        p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        forged = f"{header}.{p_b64}.AAAA"

        old_key = cb.CLERK_JWT_KEY
        blocked = {
            "cryptography.hazmat.primitives.serialization": None,
            "cryptography.hazmat.primitives.asymmetric.padding": None,
            "cryptography.hazmat.primitives.hashes": None,
        }
        saved = {k: sys.modules.get(k) for k in blocked}
        sys.modules.update(blocked)
        cb.CLERK_JWT_KEY = "-----BEGIN PUBLIC KEY-----\nMA==\n-----END PUBLIC KEY-----"
        try:
            ok, payload_out, reason = cb.verify_jwt(forged)
            self.assertFalse(ok)
            self.assertIn("cryptography", reason.lower())
            # Callers gate on `valid`, so the forged claims must never reach auth paths.
        finally:
            cb.CLERK_JWT_KEY = old_key
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_identity_ignores_caller_supplied_email(self):
        """C3: a JWT for alice must never resolve to an attacker-chosen victim email."""
        cb = self._import_cb()

        class FakeHeaders:
            def __init__(self, d):
                self._d = d

            def get(self, k, default=""):
                return self._d.get(k, default)

        token = cb.create_jwt({"email": "alice@test.com"})
        handler = cb.CloudRequestHandler.__new__(cb.CloudRequestHandler)
        handler.headers = FakeHeaders({"Authorization": f"Bearer {token}"})

        email, claims = handler._get_authenticated_identity(candidate_email="victim@tokensafe.dev")
        self.assertEqual(email, "alice@test.com")
        self.assertEqual(claims.get("type"), "jwt")

    def test_clerk_resolution_failure_is_negatively_cached(self):
        """Resilience: while Clerk Backend API is down, lookups fail fast (one attempt per 30s)."""
        cb = self._import_cb()
        from unittest.mock import patch

        cb._CLERK_USER_CACHE.pop("user_outage_1", None)
        cb._CLERK_RESOLVE_FAILURES.pop("user_outage_1", None)
        old_secret = os.environ.get("CLERK_SECRET_KEY")
        os.environ["CLERK_SECRET_KEY"] = "sk_test_clerk_secret"
        try:
            calls = {"n": 0}

            def flaky_urlopen(req, timeout=None):
                calls["n"] += 1
                raise IOError("clerk api unreachable")

            with patch.object(cb.urllib.request, "urlopen", side_effect=flaky_urlopen):
                r1 = cb._resolve_clerk_user("user_outage_1")
                r2 = cb._resolve_clerk_user("user_outage_1")
            self.assertIsNone(r1)
            self.assertIsNone(r2)
            self.assertEqual(calls["n"], 1, "second lookup must be served by the negative cache")
            self.assertIn("user_outage_1", cb._CLERK_RESOLVE_FAILURES)
        finally:
            if old_secret is None:
                os.environ.pop("CLERK_SECRET_KEY", None)
            else:
                os.environ["CLERK_SECRET_KEY"] = old_secret
            cb._CLERK_RESOLVE_FAILURES.pop("user_outage_1", None)

    def test_forged_ts1_license_rejected_server_side(self):
        """H1: locally-forged TS1 keys (valid HMAC, public salt) fail the issuance check."""
        cb = self._import_cb()
        from src.license_manager import LicenseManager, ROTATED_HMAC_SALT
        forged = LicenseManager.generate_license(
            tier="enterprise", email="owner@bigco.com", version=1
        )
        # Signature is valid offline (salt is public)…
        ok, _, _ = LicenseManager.validate_license(forged)
        self.assertTrue(ok)
        # …but the server-side issuance gate must reject unknown TS1 keys.
        # (Test DB is a real SQLite cloud DB via init_cloud_db in other tests; a key
        # we just fabricated locally was never issued.)
        self.assertFalse(cb.CloudBackendService.is_issued_license(forged))
        self.assertTrue(cb.CloudBackendService.is_issued_license("TS2." + "a" * 40))

    def test_team_cache_ttl_purges_stale_entries(self):
        """Team cache retention: entries idle beyond the TTL are pruned on writes."""
        cb = self._import_cb()
        cb.init_cloud_db()
        svc = cb.CloudBackendService
        stale_cutoff = int(__import__("time").time()) - (svc.TEAM_CACHE_TTL_DAYS + 1) * 86400
        with cb.get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO team_cache (team_id, cache_type, key_hash, cached_value, tokens_saved_estimate, created_at, last_accessed_at, hit_count) VALUES (?,?,?,?,?,?,?,0)",
                ("ttltest@team.io", "PROMPT", "deadbeef", "(DEBUG x)", 0, stale_cutoff, stale_cutoff)
            )
            conn.commit()
        # Force the amortized purge window and trigger it via a fresh put
        svc._TEAM_CACHE_LAST_PURGE = 0.0
        self.assertTrue(svc.team_cache_put("ttltest@team.io", "PROMPT", "cafebabe", "(DEBUG y)"))
        with cb.get_db_connection() as conn:
            stale = conn.execute(
                "SELECT COUNT(*) FROM team_cache WHERE team_id = ? AND key_hash = 'deadbeef'",
                ("ttltest@team.io",)
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM team_cache WHERE team_id = ? AND key_hash = 'cafebabe'",
                ("ttltest@team.io",)
            ).fetchone()[0]
            conn.execute("DELETE FROM team_cache WHERE team_id = ?", ("ttltest@team.io",))
            conn.commit()
        self.assertEqual(stale, 0, "stale entry beyond TTL must be purged")
        self.assertEqual(fresh, 1, "fresh entry must survive")


class TestMcpFirewallWiring(unittest.TestCase):
    """H2: smart_read_file results populate the dedup cache; second read is a cache hit."""

    def test_smart_read_file_second_call_hits_firewall_cache(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_mcp_")
        old_data = os.environ.get("TOKENSAFE_DATA_DIR")
        old_db = os.environ.get("TOKENSAFE_DB_PATH")
        os.environ["TOKENSAFE_DATA_DIR"] = tmp
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
        try:
            target = os.path.join(tmp, "sample.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write("def alpha():\n    return 1\n\ndef beta():\n    return 2\n")

            from src import mcp_server

            r1 = mcp_server.handle_tool_call("smart_read_file", {"path": target})
            self.assertFalse(r1.get("isError"))
            text1 = r1["content"][0]["text"]
            self.assertNotIn("Subagent Firewall", text1)

            r2 = mcp_server.handle_tool_call("smart_read_file", {"path": target})
            text2 = r2["content"][0]["text"]
            self.assertIn("Subagent Firewall", text2)  # dedup header => cache wired
        finally:
            for key, val in (("TOKENSAFE_DATA_DIR", old_data), ("TOKENSAFE_DB_PATH", old_db)):
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(sys.platform == "darwin", "macOS-only updater dialog")
class TestMacOsUpdaterDialog(unittest.TestCase):
    """The Update Now button must run via a login shell so profile PATHs (nvm etc.) resolve."""

    def test_dialog_uses_login_shell_wrapper(self):
        from unittest.mock import patch
        from src.version_checker import VersionChecker

        captured = {}
        class FakePopen:
            def __init__(self, args, **kwargs):
                captured["args"] = args

        with patch("src.version_checker.subprocess.Popen", FakePopen):
            ok = VersionChecker.show_os_dialog(
                current_version="1.4.13", latest_version="1.4.14", update_command="tokensafe update"
            )
        self.assertTrue(ok)
        script = captured["args"][2]
        self.assertIn("display dialog", script)
        # login-shell wrapper resolves nvm/homebrew PATH profiles…
        self.assertIn("-lc", script)
        self.assertIn("${SHELL:-/bin/zsh}", script)
        # …the update command and its log are preserved
        self.assertIn("tokensafe update", script)
        self.assertIn("/tmp/tokensafe-update.log", script)
        # …and the command is safely single-quoted for the login shell
        self.assertIn("'nohup tokensafe update", script)

    def test_posix_single_quote_escapes(self):
        from src.version_checker import VersionChecker
        self.assertEqual(VersionChecker._posix_single_quote("plain"), "'plain'")
        self.assertEqual(VersionChecker._posix_single_quote("it's"), "'it'\\''s'")


class TestMetricsHonestySplit(unittest.TestCase):
    """measured | estimated labelling end-to-end: local DB, summary split, cloud telemetry."""

    def setUp(self):
        from src import metrics as metrics_mod
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_honesty_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.db_path = os.path.join(self._tmp, "metrics.db")
        self._prev = os.environ.get("TOKENSAFE_DB_PATH")
        self._prev_tel = os.environ.get("TOKENSAFE_TELEMETRY_DISABLED")
        os.environ["TOKENSAFE_DB_PATH"] = self.db_path
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        from src.metrics import MetricsTracker
        self.tracker = MetricsTracker()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._prev
        if self._prev_tel is None:
            os.environ.pop("TOKENSAFE_TELEMETRY_DISABLED", None)
        else:
            os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = self._prev_tel

    def test_labels_resolved_by_type_map_and_override(self):
        r_tool = self.tracker.record_event("s", "TOOL_READ", 100, 30)
        r_fold = self.tracker.record_event("s", "HISTORY_FOLD", 1000, 100)
        r_graph = self.tracker.record_event("s", "GRAPH_TOPOLOGY", 5000, 500)
        r_prompt = self.tracker.record_event("s", "PROMPT", 500, 400)
        r_forced = self.tracker.record_event("s", "PROMPT", 200, 100, measurement="measured")
        self.assertEqual(r_tool["measurement"], "measured")
        self.assertEqual(r_fold["measurement"], "estimated")
        self.assertEqual(r_graph["measurement"], "estimated")
        self.assertEqual(r_prompt["measurement"], "estimated")
        self.assertEqual(r_forced["measurement"], "measured")

    def test_summary_split_sums(self):
        self.tracker.record_event("s", "TOOL_READ", 100, 30)        # +70 measured
        self.tracker.record_event("s", "COMMAND_FILTER", 400, 100)  # +300 measured
        self.tracker.record_event("s", "HISTORY_FOLD", 1000, 100)   # +900 estimated
        s = self.tracker.get_summary()
        self.assertEqual(s["total_saved_tokens"], 1270)
        self.assertEqual(s["total_saved_tokens_measured"], 370)
        self.assertEqual(s["total_saved_tokens_estimated"], 900)

    def test_prune_rollup_keeps_estimated_share(self):
        self.tracker.record_event("s", "HISTORY_FOLD", 1000, 100)   # 900 estimated (older id)
        self.tracker.record_event("s", "TOOL_READ", 100, 30)        # 70 measured  (kept)
        res = self.tracker.prune(max_events=1)  # roll the oldest event into lifetime
        self.assertEqual(res["pruned_events"], 1)
        s = self.tracker.get_summary()
        # totals preserved across the rollup: 900 estimated (lifetime) + 70 measured (active)
        self.assertEqual(s["total_saved_tokens"], 970)
        self.assertEqual(s["total_saved_tokens_measured"], 70)
        self.assertEqual(s["total_saved_tokens_estimated"], 900)
        self.assertEqual(s["breakdown"]["HISTORY_FOLD"]["estimated_saved"], 900)

    def test_legacy_rows_relabelled_on_migration(self):
        # Simulate a pre-split database row (labeled 'measured' by the old schema default).
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO events (session_id, timestamp, event_type, original_tokens,"
            " optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details, measurement)"
            " VALUES ('s', '2026-01-01', 'GRAPH_TOPOLOGY', 100, 10, 90, 90, 0.0001, '{}', 'measured')"
        )
        conn.commit()
        conn.close()
        # Force the init path to run again so the retro-labelling UPDATE fires.
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        from src.metrics import MetricsTracker
        MetricsTracker(self.db_path)
        s = self.tracker.get_summary()
        self.assertEqual(s["total_saved_tokens_estimated"], 90)
        self.assertEqual(s["total_saved_tokens_measured"], 0)


@unittest.skipIf(
    not os.path.exists(os.path.join(PROJECT_ROOT, "src", "cloud_backend.py")),
    "cloud backend not distributed"
)
class TestCloudTelemetryMeasurementSplit(unittest.TestCase):
    """Cloud record_telemetry labels + dashboard/admin split (shared local cloud.db pattern)."""

    def setUp(self):
        from src.cloud_backend import (
            CloudBackendService, init_cloud_db, get_db_connection, create_jwt
        )
        self.svc = CloudBackendService
        self.create_jwt = create_jwt
        init_cloud_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM telemetry WHERE customer_email LIKE '%@example.com'")
            conn.commit()

    def tearDown(self):
        from src.cloud_backend import get_db_connection
        with get_db_connection() as conn:
            conn.execute("DELETE FROM telemetry WHERE customer_email LIKE '%@example.com'")
            conn.commit()

    def test_record_telemetry_labels_and_dashboard_split(self):
        email = "split_test@example.com"
        self.svc.handle_stripe_checkout(email=email, tier="solo")
        events = [
            {"event_type": "TOOL_READ", "original_tokens": 100, "saved_tokens": 70, "est_usd_saved": 0.0001},
            {"event_type": "HISTORY_FOLD", "original_tokens": 1000, "saved_tokens": 900, "est_usd_saved": 0.0014},
            {"event_type": "PROMPT", "original_tokens": 500, "saved_tokens": 100,
             "est_usd_saved": 0.0001, "measurement": "measured"},  # explicit override honored
        ]
        cnt = self.svc.record_telemetry(email, "", events)
        self.assertEqual(cnt, 3)
        data = self.svc.get_dashboard_data(email)
        m = data["metrics"]
        self.assertEqual(m["total_saved_tokens"], 1070)
        self.assertEqual(m["saved_tokens_measured"], 170)   # 70 + 100 (override)
        self.assertEqual(m["saved_tokens_estimated"], 900)  # HISTORY_FOLD

    def test_admin_overview_no_fabricated_fallbacks(self):
        admin_email = "nekosin4eg@gmail.com"
        token = self.create_jwt({"email": admin_email})
        overview = self.svc.get_admin_overview(token=token)
        self.assertTrue(overview.get("authorized"))
        # The shared dev DB may hold real telemetry; the contract is the LABELS exist
        # and the ratio is a computed number, never the old fabricated 74.x constant.
        self.assertIsInstance(overview["overview"]["overall_reduction_pct"], float)
        self.assertNotIn(str(overview["overview"]["overall_reduction_pct"]), ("74.2", "74.5"))
        self.assertIn("saved_tokens_measured", overview["overview"])
        self.assertIn("saved_tokens_estimated", overview["overview"])
        for lic in overview["overview"].get("licenses", []):
            self.assertNotEqual(lic["client_version"], "1.3.4")


@unittest.skipIf(
    not os.path.exists(os.path.join(PROJECT_ROOT, "src", "cloud_backend.py")),
    "cloud backend not distributed"
)
class TestPublicRateLimit(unittest.TestCase):
    """Sliding-window limiter on public endpoints; /health must stay exempt."""

    def test_limiter_blocks_after_budget_and_recovers(self):
        from src.cloud_backend import _is_rate_limited, _RATE_LIMIT_BUCKETS
        _RATE_LIMIT_BUCKETS.pop("t:limit", None)
        # budget of 3: first 3 pass, 4th is blocked
        results = [_is_rate_limited("t:limit", limit_per_minute=3) for _ in range(4)]
        self.assertEqual(results, [False, False, False, True])
        # window slides: backdate all hits so the bucket drains
        import time as _time
        _RATE_LIMIT_BUCKETS["t:limit"] = [_time.time() - 120] * 3
        self.assertFalse(_is_rate_limited("t:limit", limit_per_minute=3))
        _RATE_LIMIT_BUCKETS.pop("t:limit", None)

    def test_limiter_disabled_via_env_zero(self):
        from src.cloud_backend import _is_rate_limited, _RATE_LIMIT_BUCKETS
        _RATE_LIMIT_BUCKETS.pop("t:off", None)
        self.assertFalse(any(_is_rate_limited("t:off", limit_per_minute=0) for _ in range(50)))
        _RATE_LIMIT_BUCKETS.pop("t:off", None)

    def test_health_endpoint_not_rate_limited(self):
        # /health must never be throttled (Bunny probes); assert the route block
        # in the handler contains no limiter call.
        import inspect
        import re
        from src.cloud_backend import CloudRequestHandler
        src = inspect.getsource(CloudRequestHandler._handle_get)
        m = re.search(
            r'if path in \("/health", "/api/health"\):.*?(?=\n        elif |\Z)', src, re.S
        )
        self.assertIsNotNone(m, "health route block not found")
        self.assertNotIn("_is_rate_limited", m.group(0))


@unittest.skipIf(
    not os.path.exists(os.path.join(PROJECT_ROOT, "src", "cloud_backend.py")),
    "cloud backend not distributed"
)
class TestTS1SunsetMigration(unittest.TestCase):
    """check_version transparently reissues TS1 keys as TS2 and the client swaps them."""

    def setUp(self):
        from src.cloud_backend import CloudBackendService, init_cloud_db, get_db_connection
        self.svc = CloudBackendService
        init_cloud_db()
        self._conn_ctx = get_db_connection
        with get_db_connection() as conn:
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@ts1-migrate.com'")
            conn.execute("DELETE FROM customers WHERE email LIKE '%@ts1-migrate.com'")
            conn.commit()

    def tearDown(self):
        with self._conn_ctx() as conn:
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@ts1-migrate.com'")
            conn.execute("DELETE FROM customers WHERE email LIKE '%@ts1-migrate.com'")
            conn.commit()

    def test_ts1_key_reissued_as_ts2(self):
        from src.license_manager import LicenseManager
        email = "legacy@ts1-migrate.com"
        old_key = LicenseManager.generate_license(tier="solo", email=email, duration_days=45, version=1)
        self.assertTrue(old_key.startswith("TS1."))
        now = int(time.time())
        with self._conn_ctx() as conn:
            conn.execute(
                "INSERT INTO licenses (license_key, customer_email, tier, is_trial, is_seat, parent_email, status, created_at, expires_at)"
                " VALUES (?, ?, 'solo', 0, 0, NULL, 'active', ?, ?)",
                (old_key, email, now, now + 45 * 86400)
            )
            conn.commit()

        resp = self.svc.check_version(client_version="1.4.15", license_key=old_key)
        self.assertTrue(resp.get("ts1_deprecated"))
        new_key = resp.get("reissued_license_key", "")
        self.assertTrue(new_key.startswith("TS2."))
        self.assertIn("TS2 Ed25519", resp["message"])

        # New key verifies and carries the same identity with preserved duration
        valid, payload, _ = LicenseManager.validate_license(new_key)
        self.assertTrue(valid)
        self.assertEqual(payload["email"], email)
        self.assertGreaterEqual(payload["expires_at"], now + 44 * 86400)

        # The row now points at the TS2 key; the TS1 key is retired server-side
        with self._conn_ctx() as conn:
            row = conn.execute(
                "SELECT license_key FROM licenses WHERE customer_email = ?", (email,)
            ).fetchone()
        self.assertEqual(row["license_key"], new_key)

        # Second contact with the OLD key: no issued row anymore => no reissue loop
        resp2 = self.svc.check_version(client_version="1.4.15", license_key=old_key)
        self.assertNotIn("reissued_license_key", resp2)

    def test_client_swaps_reissued_key_and_never_caches_it(self):
        import importlib
        from unittest.mock import patch, MagicMock
        from src import version_checker as vc_mod
        email = "swap@ts1-migrate.com"
        new_key = LicenseManager_generate_ts2(email)

        saved = {}
        def fake_urlopen(req, timeout=1.5):
            body = json.dumps({
                "status": "up_to_date", "client_version": "1.4.15",
                "latest_version": "1.4.15", "update_available": False,
                "ts1_deprecated": True, "reissued_license_key": new_key,
            }).encode("utf-8")
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = body
            ctx = MagicMock()
            ctx.__enter__.return_value = resp
            return ctx

        with patch.object(vc_mod.VersionChecker, "save_cache", side_effect=lambda d: saved.update(d)), \
             patch.object(vc_mod.VersionChecker, "load_cache", return_value=None), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("src.license_manager.LicenseManager.save_license") as fake_save:
            data = vc_mod.VersionChecker.check_version(current_version="1.4.15", force_fetch=True)
        fake_save.assert_called_once_with(new_key)
        self.assertNotIn("reissued_license_key", data)
        self.assertNotIn("reissued_license_key", saved)


def LicenseManager_generate_ts2(email: str) -> str:
    from src.license_manager import LicenseManager
    return LicenseManager.generate_license(tier="solo", email=email, duration_days=45)


class TestProxyReplaceModeDedup(unittest.TestCase):
    """Proxy replace-mode heartbeat: hook skips prompt compaction while the proxy
    is actively replacing prompts (no double-count, no double-injection)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_proxy_hb_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._prev = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = self._tmp

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
        else:
            os.environ["TOKENSAFE_DATA_DIR"] = self._prev

    def test_heartbeat_marker_roundtrip(self):
        from src.proxy import TokenSafeProxyHandler
        import time as _time
        marker = os.path.join(self._tmp, ".proxy_active")
        self.assertFalse(os.path.exists(marker))
        TokenSafeProxyHandler._touch_proxy_heartbeat()
        self.assertTrue(os.path.exists(marker))
        self.assertLessEqual(_time.time() - os.path.getmtime(marker), 5)

    def test_hook_skips_compaction_while_proxy_active(self):
        # Fresh heartbeat => hook must not inject a TOCP copy nor record PROMPT
        from src.proxy import TokenSafeProxyHandler
        from src import metrics as metrics_mod
        TokenSafeProxyHandler._touch_proxy_heartbeat()
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)

        payload = json.dumps({
            "lastUserInput": "please refactor the auth module to use async handlers",
            "conversationId": "proxy_dedup_test",
            "invocationNum": 1,
            "transcriptPath": ""
        })
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "pre_invocation.py")],
            input=payload, capture_output=True, text=True, timeout=60,
            env={**os.environ}
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout or "{}")
        serialized = json.dumps(out)
        self.assertNotIn("[TOCP Compressed Intent]", serialized)
        from src.metrics import MetricsTracker
        t = MetricsTracker()
        s = t.get_summary()
        self.assertEqual(s["total_events"], 0)

    def test_hook_compacts_again_when_heartbeat_stale(self):
        # Stale heartbeat (>120s) => hook resumes its own compaction
        import time as _time
        marker = os.path.join(self._tmp, ".proxy_active")
        with open(marker, "w") as f:
            f.write(str(int(_time.time())))
        os.utime(marker, (_time.time() - 300, _time.time() - 300))
        from src import metrics as metrics_mod
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)

        payload = json.dumps({
            "lastUserInput": (
                "Now I want you to carefully review and refactor the entire authentication module "
                "in the src/auth directory to use async handlers instead of blocking calls because "
                "the current implementation blocks the event loop"
            ),
            "conversationId": "proxy_dedup_stale_test",
            "invocationNum": 1,
            "transcriptPath": ""
        })
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "pre_invocation.py")],
            input=payload, capture_output=True, text=True, timeout=60,
            env={**os.environ}
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout or "{}")
        self.assertIn("[TOCP Compressed Intent]", json.dumps(out))


class TestTeamCachePrewarm(unittest.TestCase):
    """smart_read_file serves teammate skeletons from the team cache and republishes fresh ones."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_prewarm_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.py_file = os.path.join(self._tmp, "svc.py")
        with open(self.py_file, "w", encoding="utf-8") as f:
            f.write("def alpha(x):\n" + "    y = x + 1\n" * 30 + "    return y\n\n\ndef beta():\n    return alpha(1)\n")

    def test_lookup_skips_network_for_non_team(self):
        from unittest.mock import patch
        from src import mcp_server
        with patch.object(mcp_server.TeamCache, "_is_team_tier", return_value=(False, "")), \
             patch.object(mcp_server.TeamCache, "get") as fake_get:
            self.assertIsNone(mcp_server._team_skeleton_lookup(self.py_file, "alpha"))
            fake_get.assert_not_called()  # non-team: no network call at all

    def test_hit_returns_teammate_skeleton(self):
        from unittest.mock import patch
        from src import mcp_server
        teammate_skel = "def alpha(x):\n    ..."
        with patch.object(mcp_server.TeamCache, "_is_team_tier", return_value=(True, "TS2.x")), \
             patch.object(mcp_server.TeamCache, "get", return_value=teammate_skel):
            res = mcp_server.handle_tool_call("smart_read_file", {"path": self.py_file, "symbol": "alpha"})
        text = "".join(p.get("text", "") for p in res.get("content", []))
        self.assertIn("Team-Cached Skeleton", text)
        self.assertIn("def alpha", text)
        # Cache-hit is metered in local metrics as its own event type
        summary = mcp_server._get_safe_tracker().get_summary()
        self.assertGreaterEqual(summary["breakdown"].get("TEAM_CACHE_HIT", {}).get("count", 0), 1)

    def test_prewarm_never_blocks_or_raises(self):
        from unittest.mock import patch
        from src import mcp_server
        with patch.object(mcp_server.TeamCache, "_is_team_tier", side_effect=RuntimeError("boom")):
            # must swallow the error and return quickly
            self.assertIsNone(mcp_server._team_skeleton_lookup(self.py_file, "alpha"))
        with patch.object(mcp_server.TeamCache, "put", side_effect=RuntimeError("boom")):
            mcp_server._team_skeleton_prewarm(self.py_file, "alpha", "def alpha: ...", 100)  # no raise


if __name__ == "__main__":
    unittest.main()
