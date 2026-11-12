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

    def test_mtime_only_change_still_hits(self):
        """Diff-aware contract: mtime lies after `git checkout`; unchanged
        content must still HIT (stage-2 hash lookup), now across sessions."""
        session = "swarm_session_2"
        file_path = self.tmp.name

        AgentFirewall.record_cache("other_session", file_path, "def worker():\n    return 42\n", tracker=self.tracker)

        # git-checkout-style mtime reset (file content untouched)
        future_time = os.path.getmtime(file_path) + 10
        os.utime(file_path, (future_time, future_time))

        hit, content, saved = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertTrue(hit, "unchanged content must hit across sessions despite mtime reset")
        self.assertIn("Persistent cache", content)
        self.assertIn("return 42", content)

    def test_cache_invalidates_on_content_change(self):
        """A REAL edit (hash mismatch) invalidates the entry, mtime aside."""
        session = "swarm_session_3"
        file_path = self.tmp.name

        AgentFirewall.record_cache(session, file_path, "v1 content", tracker=self.tracker)

        with open(file_path, "w") as f:
            f.write("def worker():\n    return 43\n")  # real content change
        future_time = os.path.getmtime(file_path) + 10
        os.utime(file_path, (future_time, future_time))

        hit, content, saved = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertFalse(hit, "changed content must MISS even with fresh mtime")

    def test_oversized_content_not_persisted(self):
        session = "swarm_session_4"
        AgentFirewall.record_cache(session, self.tmp.name, "x" * (AgentFirewall._MAX_CONTENT_CHARS + 1), tracker=self.tracker)
        hit, _, _ = AgentFirewall.check_cache(session, self.tmp.name, tracker=self.tracker)
        self.assertFalse(hit)

    def test_static_header_deterministic_across_hits(self):
        session = "swarm_static_hdr"
        file_path = self.tmp.name
        AgentFirewall.record_cache(session, file_path, "def worker():\n    return 42\n", tracker=self.tracker)
        _, content1, _ = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        _, content2, _ = AgentFirewall.check_cache(session, file_path, tracker=self.tracker)
        self.assertEqual(content1, content2)
        self.assertNotIn("Dedup Hit #", content1)

    def test_pointer_mode_returns_compact_pointer_when_allowed(self):
        session = "mcp_pointer_sess"
        file_path = self.tmp.name
        big_content = "\n".join(f"def fn_{i}(): return {i}" for i in range(40))
        AgentFirewall.record_cache(session, file_path, big_content, symbol="fn_0", tracker=self.tracker)
        hit, ptr, saved = AgentFirewall.check_cache(
            session, file_path, symbol="fn_0", tracker=self.tracker, allow_pointer=True
        )
        self.assertTrue(hit)
        self.assertIn("Subagent Firewall: Pointer Hit", ptr)
        self.assertIn("force_full=true", ptr)
        self.assertGreater(saved, 100)

    def test_symbol_isolation_and_dropped_table_self_healing(self):
        session = "swarm_heal"
        file_path = self.tmp.name
        AgentFirewall.record_cache(session, file_path, "def fn_a(): return 1", symbol="fn_a", tracker=self.tracker)
        hit_other_sym, _, _ = AgentFirewall.check_cache(session, file_path, symbol="fn_b", tracker=self.tracker)
        self.assertFalse(hit_other_sym)

        # Simulate table loss while _INITIALIZED_DBS still holds db_path
        with self.tracker._get_connection() as conn:
            conn.execute("DROP TABLE subagent_file_cache")
        hit_after_drop, _, _ = AgentFirewall.check_cache(session, file_path, symbol="fn_a", tracker=self.tracker)
        self.assertFalse(hit_after_drop)
        AgentFirewall.record_cache(session, file_path, "def fn_a(): return 1", symbol="fn_a", tracker=self.tracker)
        hit_recreated, out, _ = AgentFirewall.check_cache(session, file_path, symbol="fn_a", tracker=self.tracker)
        self.assertTrue(hit_recreated)
        self.assertIn("return 1", out)

if __name__ == "__main__":
    unittest.main()
