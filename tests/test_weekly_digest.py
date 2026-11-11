import os
os.environ["TOKENSAFE_PRIVATE_KEY"] = "db13e3ddc4a9856417ad2236b2a4e96cf62b5a825dd044409b3f693bc77ff700"
os.environ["TOKENSAFE_PUBLIC_KEY"] = "8a2c9d550180ab5b00a06174cb6aecfd712740c59d0bdcff66a56515348c855e"

import datetime
import sqlite3
import unittest
from unittest.mock import patch
import time
from src.weekly_digest import WeeklyDigestGenerator, format_token_number

try:
    from src.cloud_backend import CloudBackendService, init_cloud_db, get_db_connection
    from src.mailer import TokenSafeMailer
except ImportError:
    CloudBackendService = None
    init_cloud_db = None
    get_db_connection = None
    TokenSafeMailer = None

@unittest.skipIf(CloudBackendService is None, "src.cloud_backend is a server-side component not distributed in client repo")
class TestWeeklyDigest(unittest.TestCase):
    def _cleanup(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM customers WHERE email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM organizations WHERE owner_email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@digest-test.com' OR parent_email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM telemetry WHERE customer_email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM digest_dispatches WHERE owner_email LIKE '%@digest-test.com'")
            conn.commit()

    def setUp(self):
        init_cloud_db()
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def test_token_number_formatter(self):
        self.assertEqual(format_token_number(4_200_000), "4.2M")
        self.assertEqual(format_token_number(850_000), "850.0K")
        self.assertEqual(format_token_number(450), "450")

    def test_weekly_digest_generation_and_leaderboard(self):
        teamlead = "teamlead@digest-test.com"
        eng1 = "engineer1@digest-test.com"
        eng2 = "engineer2@digest-test.com"

        # 1. Provision team
        CloudBackendService.handle_stripe_checkout(email=teamlead, tier="team", seats=5, is_trial=False)
        seat1 = CloudBackendService.generate_team_seat(teamlead, eng1)
        seat2 = CloudBackendService.generate_team_seat(teamlead, eng2)

        # 2. Record telemetry
        now = int(time.time())
        CloudBackendService.record_telemetry(eng1, seat1["license_key"], [
            {"event_type": "PROMPT", "original_tokens": 10000, "saved_tokens": 7000, "usd_saved": 0.05, "timestamp": now}
        ])
        CloudBackendService.record_telemetry(eng2, seat2["license_key"], [
            {"event_type": "AST_SKELETON", "original_tokens": 20000, "saved_tokens": 16000, "usd_saved": 0.12, "timestamp": now}
        ])

        # 3. Compile digest
        digest = WeeklyDigestGenerator.get_team_weekly_stats(teamlead)
        self.assertIsNotNone(digest)
        self.assertEqual(digest["owner_email"], teamlead)
        self.assertEqual(digest["total_saved_tokens"], 23000)
        self.assertAlmostEqual(digest["total_usd_saved"], 0.17, places=2)
        self.assertEqual(len(digest["leaderboard"]), 3) # teamlead + eng1 + eng2

        # Engineer 2 saved 16k tokens, should be rank 1
        top_eng = digest["leaderboard"][0]
        self.assertEqual(top_eng["email"], eng2)
        self.assertEqual(top_eng["rank"], 1)

        # 4. Render email templates
        html = WeeklyDigestGenerator.render_email_html(digest)
        self.assertIn("TokenSafe saved your team", html)
        self.assertIn(eng2.split("@")[0], html)
        self.assertIn("WEEKLY DIGEST • FRIDAY", html)

        text = WeeklyDigestGenerator.render_email_text(digest)
        self.assertIn("ENGINEER SAVINGS LEADERBOARD", text)
        self.assertIn(eng2, text)

        # 5. Dispatch via mailer & cloud backend
        ok = TokenSafeMailer.send_weekly_digest(teamlead, digest, sync=True)
        self.assertTrue(ok)

        dispatch_res = CloudBackendService.dispatch_weekly_digests(force=True, target_email=teamlead)
        self.assertEqual(dispatch_res["status"], "success")
        self.assertEqual(dispatch_res["dispatched"], 1)

    def test_uses_cloud_db_connection_when_cloud_enabled_and_local_file_absent(self):
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        now = int(time.time())
        mem.executescript(
            """
            CREATE TABLE customers (email TEXT UNIQUE, tier TEXT, seats_total INTEGER, status TEXT);
            CREATE TABLE organizations (id TEXT PRIMARY KEY, owner_email TEXT, tier TEXT, seats_total INTEGER, status TEXT, is_primary INTEGER, created_at INTEGER);
            CREATE TABLE licenses (license_key TEXT PRIMARY KEY, customer_email TEXT, parent_email TEXT, org_id TEXT);
            CREATE TABLE telemetry (customer_email TEXT, event_type TEXT, original_tokens INTEGER, saved_tokens INTEGER, usd_saved REAL, timestamp INTEGER);
            """
        )
        mem.execute("INSERT INTO customers VALUES ('cloud@digest-test.com', 'team', 5, 'active')")
        mem.execute("INSERT INTO telemetry VALUES ('cloud@digest-test.com', 'AST_SKELETON', 1000, 800, 0.02, ?)", (now,))
        mem.commit()

        from contextlib import contextmanager
        @contextmanager
        def fake_conn():
            yield mem

        with patch("src.cloud.db.get_db_connection", fake_conn), \
             patch("src.cloud.config.CLOUD_DB_URL", "https://bunny.example"), \
             patch("src.cloud.config.CLOUD_DB_TOKEN", "tok_123"), \
             patch("src.cloud.config.CLOUD_DB_PATH", "/tmp/nonexistent-tokensafe-cloud-test.db"):
            digest = WeeklyDigestGenerator.get_team_weekly_stats("cloud@digest-test.com", now=now)
        mem.close()

        self.assertIsNotNone(digest)
        self.assertEqual(digest["total_saved_tokens"], 800)

    def test_forced_target_for_solo_and_unknown_user_and_mailer_failure(self):
        solo_user = "solo@digest-test.com"
        CloudBackendService.handle_stripe_checkout(email=solo_user, tier="solo", seats=1, is_trial=False)

        # Forced targeted dispatch succeeds even on solo tier
        res_ok = CloudBackendService.dispatch_weekly_digests(force=True, target_email=solo_user)
        self.assertEqual(res_ok["status"], "success")
        self.assertEqual(res_ok["dispatched"], 1)

        # Unknown account returns explicit error instead of silent 0 dispatched
        res_missing = CloudBackendService.dispatch_weekly_digests(force=True, target_email="missing@digest-test.com")
        self.assertEqual(res_missing["status"], "error")
        self.assertEqual(res_missing["dispatched"], 0)
        self.assertIn("No active account", res_missing["error"])

        # Mailer failure is reported synchronously instead of silently swallowed
        with patch.object(TokenSafeMailer, "_send_raw", return_value=False), \
             patch.object(TokenSafeMailer, "get_last_error", return_value="ImprovMX API HTTP 401: Unauthorized"):
            res_fail = CloudBackendService.dispatch_weekly_digests(force=True, target_email=solo_user)
        self.assertEqual(res_fail["status"], "error")
        self.assertEqual(res_fail["dispatched"], 0)
        self.assertIn("401", res_fail["error"])

    def test_friday_schedule_window_and_weekly_idempotency(self):
        owner = "friday-owner@digest-test.com"
        CloudBackendService.handle_stripe_checkout(email=owner, tier="team", seats=5, is_trial=False)

        thursday = datetime.datetime(2026, 11, 12, 16, 0, tzinfo=datetime.timezone.utc)
        res_thu = CloudBackendService.dispatch_weekly_digests(force=False, target_email=owner, now_dt=thursday)
        self.assertEqual(res_thu["status"], "skipped")

        friday_early = datetime.datetime(2026, 11, 13, 10, 0, tzinfo=datetime.timezone.utc)
        res_early = CloudBackendService.dispatch_weekly_digests(force=False, target_email=owner, now_dt=friday_early)
        self.assertEqual(res_early["status"], "skipped")

        friday_window = datetime.datetime(2026, 11, 13, 15, 0, tzinfo=datetime.timezone.utc)
        res_fri = CloudBackendService.dispatch_weekly_digests(force=False, target_email=owner, now_dt=friday_window)
        self.assertEqual(res_fri["status"], "success")
        self.assertEqual(res_fri["dispatched"], 1)

        # Second automated check in the same ISO week deduplicates cleanly
        friday_later = datetime.datetime(2026, 11, 13, 17, 0, tzinfo=datetime.timezone.utc)
        res_dup = CloudBackendService.dispatch_weekly_digests(force=False, target_email=owner, now_dt=friday_later)
        self.assertEqual(res_dup["status"], "success")
        self.assertEqual(res_dup["dispatched"], 0)
        self.assertTrue(res_dup["results"][0].get("skipped"))

        # Next Friday dispatches again
        next_friday = datetime.datetime(2026, 11, 20, 15, 0, tzinfo=datetime.timezone.utc)
        res_next = CloudBackendService.dispatch_weekly_digests(force=False, target_email=owner, now_dt=next_friday)
        self.assertEqual(res_next["status"], "success")
        self.assertEqual(res_next["dispatched"], 1)

if __name__ == "__main__":
    unittest.main()
