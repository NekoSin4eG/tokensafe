import unittest
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
    def setUp(self):
        init_cloud_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM customers WHERE email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@digest-test.com' OR parent_email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM telemetry WHERE customer_email LIKE '%@digest-test.com'")
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM customers WHERE email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@digest-test.com' OR parent_email LIKE '%@digest-test.com'")
            conn.execute("DELETE FROM telemetry WHERE customer_email LIKE '%@digest-test.com'")
            conn.commit()

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

if __name__ == "__main__":
    unittest.main()
