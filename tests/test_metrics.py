import unittest
import tempfile
import os
from src.metrics import MetricsTracker

class TestMetricsTracker(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.tracker = MetricsTracker(db_path=self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_record_and_summary(self):
        res = self.tracker.record_event(
            session_id="test_sess_1",
            event_type="PROMPT",
            original_tokens=100,
            optimized_tokens=40,
            details={"action": "REFACTOR"}
        )
        self.assertEqual(res["saved_tokens"], 60)
        self.assertEqual(res["reduction_pct"], 60.0)

        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_saved_tokens"], 60)
        self.assertEqual(summary["total_original_tokens"], 100)
        self.assertEqual(summary["overall_reduction_pct"], 60.0)
        self.assertEqual(summary["total_events"], 1)

    def test_reset(self):
        self.tracker.record_event("sess", "PROMPT", 50, 20)
        self.tracker.reset()
        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_saved_tokens"], 0)
        self.assertEqual(summary["total_events"], 0)

    def test_prune_by_max_events(self):
        # Record 10 events
        for i in range(10):
            self.tracker.record_event(
                session_id=f"sess_{i}",
                event_type="PROMPT" if i % 2 == 0 else "COMMAND_FILTER",
                original_tokens=100,
                optimized_tokens=40
            )

        summary_before = self.tracker.get_summary()
        self.assertEqual(summary_before["total_events"], 10)
        self.assertEqual(summary_before["total_saved_tokens"], 600)
        self.assertEqual(summary_before["total_original_tokens"], 1000)

        # Prune down to 3 most recent events
        res = self.tracker.prune(max_events=3, vacuum=True)
        self.assertEqual(res["pruned_events"], 7)

        # Raw events in db should now be 3
        recent = self.tracker.get_recent_events(limit=20)
        self.assertEqual(len(recent), 3)

        # But cumulative summary MUST preserve all 10 events and 600 saved tokens!
        summary_after = self.tracker.get_summary()
        self.assertEqual(summary_after["total_events"], 10)
        self.assertEqual(summary_after["total_saved_tokens"], 600)
        self.assertEqual(summary_after["total_original_tokens"], 1000)
        self.assertEqual(summary_after["overall_reduction_pct"], 60.0)
        self.assertEqual(summary_after["breakdown"]["PROMPT"]["count"], 5)
        self.assertEqual(summary_after["breakdown"]["COMMAND_FILTER"]["count"], 5)

    def test_prune_by_days(self):
        from datetime import datetime, timedelta
        # Insert an old event (40 days ago) and a new one
        self.tracker.record_event("sess_old", "TOOL_READ", 200, 50)
        old_time = (datetime.now() - timedelta(days=40)).isoformat()

        with self.tracker._get_connection() as conn:
            conn.execute("UPDATE events SET timestamp = ? WHERE session_id = 'sess_old'", (old_time,))

        self.tracker.record_event("sess_new", "TOOL_READ", 300, 100)

        # Prune older than 30 days
        res = self.tracker.prune(days=30, vacuum=True)
        self.assertEqual(res["pruned_events"], 1)

        recent = self.tracker.get_recent_events(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["session_id"], "sess_new")

        # Summary must still reflect both events (200-50=150 + 300-100=200 => 350 saved)
        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_events"], 2)
        self.assertEqual(summary["total_saved_tokens"], 350)
        self.assertEqual(summary["total_original_tokens"], 500)

    def test_reset_clears_lifetime_aggregates(self):
        self.tracker.record_event("s1", "PROMPT", 100, 40)
        self.tracker.prune(max_events=0, vacuum=False)  # prune all into lifetime
        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_saved_tokens"], 60)

        self.tracker.reset()
        summary_reset = self.tracker.get_summary()
        self.assertEqual(summary_reset["total_saved_tokens"], 0)
        self.assertEqual(summary_reset["total_events"], 0)
        self.assertEqual(summary_reset["breakdown"], {})

    def test_weekly_saved_window(self):
        from datetime import datetime, timedelta
        # Old event (10 days ago) must fall outside the rolling 7-day window
        self.tracker.record_event("w_old", "TOOL_READ", 200, 50)
        old_time = (datetime.now() - timedelta(days=10)).isoformat()
        with self.tracker._get_connection() as conn:
            conn.execute("UPDATE events SET timestamp = ? WHERE session_id = 'w_old'", (old_time,))
        self.tracker.record_event("w_new", "TOOL_READ", 300, 100)

        weekly = self.tracker.get_weekly_saved()
        self.assertEqual(weekly["events"], 1)
        self.assertEqual(weekly["saved_tokens"], 200)
        self.assertGreater(weekly["usd_saved"], 0)

    def test_weekly_saved_empty_db(self):
        weekly = self.tracker.get_weekly_saved()
        self.assertEqual(weekly, {"events": 0, "saved_tokens": 0, "usd_saved": 0.0})

    def test_vacuum(self):
        self.tracker.record_event("s1", "PROMPT", 100, 40)
        self.tracker.vacuum()  # Should execute without errors

if __name__ == "__main__":
    unittest.main()
