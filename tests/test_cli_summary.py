import unittest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.cli import summarize_unittest_output


class TestSummarizeUnittestOutput(unittest.TestCase):
    def test_ok_with_skipped_counts_as_success(self):
        """unittest prints 'OK (skipped=N)' on green runs with skips — the
        summary must be green, not a red cross with an empty status."""
        raw = "Ran 382 tests in 9.7s\n\nOK (skipped=39)"
        out = summarize_unittest_output(raw)
        self.assertIn("✓", out)
        self.assertNotIn("✗", out)
        self.assertIn("382", out)

    def test_bare_ok_counts_as_success(self):
        out = summarize_unittest_output("Ran 5 tests in 0.1s\n\nOK")
        self.assertIn("✓", out)

    def test_failure_stays_red_with_cause(self):
        raw = (
            "Ran 2 tests in 0.2s\n"
            "\n"
            "======================================================================\n"
            "FAIL: test_x (tests.test_x.TestX.test_x)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            '  File "tests/test_x.py", line 10, in test_x\n'
            "AssertionError: 1 != 2\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.2s\n"
            "\n"
            "FAILED (failures=1)"
        )
        out = summarize_unittest_output(raw)
        self.assertIn("✗", out)
        self.assertIn("test_x", out)

    def test_empty_output_does_not_crash(self):
        self.assertIn("unittest", summarize_unittest_output(""))


if __name__ == "__main__":
    unittest.main()
