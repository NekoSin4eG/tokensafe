import unittest
import os
import tempfile
from src.loop_detector import LoopDetector

class TestLoopDetector(unittest.TestCase):
    def setUp(self):
        self.test_session = "test_loop_sess_1"
        LoopDetector.reset_session(self.test_session)

    def tearDown(self):
        LoopDetector.reset_session(self.test_session)

    def test_loop_detection_on_third_repeat(self):
        tool = "read_file"
        args = {"Path": "/app/wallet.ts"}

        # Call 1
        is_loop, count, _ = LoopDetector.check_loop(self.test_session, tool, args)
        self.assertFalse(is_loop)
        self.assertEqual(count, 1)

        # Call 2
        is_loop, count, _ = LoopDetector.check_loop(self.test_session, tool, args)
        self.assertFalse(is_loop)
        self.assertEqual(count, 2)

        # Call 3: Loop triggered!
        is_loop, count, msg = LoopDetector.check_loop(self.test_session, tool, args)
        self.assertTrue(is_loop)
        self.assertEqual(count, 3)
        self.assertIn("Loop Guard", msg)

    def test_loop_reset_on_different_args(self):
        tool = "read_file"
        LoopDetector.check_loop(self.test_session, tool, {"Path": "/app/a.ts"})
        LoopDetector.check_loop(self.test_session, tool, {"Path": "/app/a.ts"})

        # Different argument resets
        is_loop, count, _ = LoopDetector.check_loop(self.test_session, tool, {"Path": "/app/b.ts"})
        self.assertFalse(is_loop)
        self.assertEqual(count, 1)

if __name__ == "__main__":
    unittest.main()
