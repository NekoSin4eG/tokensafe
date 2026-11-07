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

if __name__ == "__main__":
    unittest.main()
