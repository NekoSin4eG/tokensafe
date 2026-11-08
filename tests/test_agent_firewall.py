import unittest
import tempfile
import os
from src.agent_firewall import AgentFirewall
from src.metrics import MetricsTracker

class TestAgentFirewall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".py")
        self.tmp.write("def worker():\n    return 42\n")
        self.tmp.close()

        self.db_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.db_tmp.close()
        self.tracker = MetricsTracker(db_path=self.db_tmp.name)

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)
        if os.path.exists(self.db_tmp.name):
            os.remove(self.db_tmp.name)

    def test_cache_miss_then_hit(self):
        session = "swarm_session_1"
        file_path = self.tmp.name

        # Initial check -> miss
        hit, content, saved = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertFalse(hit)

        # Record cache
        read_content = "def worker():\n    return 42\n"
        AgentFirewall.record_cache(session, file_path, read_content, tracker=self.tracker)

        # Subsequent check -> HIT!
        hit2, content2, saved2 = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertTrue(hit2)
        self.assertIn("Subagent Firewall: Reused session read", content2)
        self.assertIn("return 42", content2)

    def test_cache_invalidates_on_mtime_change(self):
        session = "swarm_session_2"
        file_path = self.tmp.name

        AgentFirewall.record_cache(session, file_path, "v1", tracker=self.tracker)

        # Touch file with future mtime
        future_time = os.path.getmtime(file_path) + 10
        os.utime(file_path, (future_time, future_time))

        hit, content, saved = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertFalse(hit)

if __name__ == "__main__":
    unittest.main()
