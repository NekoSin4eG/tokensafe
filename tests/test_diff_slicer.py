import unittest
from src.diff_slicer import DiffSlicer

class TestDiffSlicer(unittest.TestCase):
    def test_strip_lockfile_diff(self):
        raw_diff = (
            "diff --git a/package-lock.json b/package-lock.json\n"
            "index 12345..67890 100644\n"
            "--- a/package-lock.json\n"
            "+++ b/package-lock.json\n"
            "@@ -1,5 +1,5 @@\n"
            "+ \"version\": \"2.0.0\"\n"
            "diff --git a/src/app.py b/src/app.py\n"
            "index abc..def 100644\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -10,3 +10,3 @@\n"
            "- old_code()\n"
            "+ new_code()\n"
        )
        sliced, orig_t, opt_t = DiffSlicer.slice_diff(raw_diff)
        self.assertIn("Omitted diff for lockfile", sliced)
        self.assertIn("new_code()", sliced)
        self.assertNotIn("\"version\": \"2.0.0\"", sliced)

    def test_context_line_compression(self):
        long_context = "".join([f" line {i}\n" for i in range(20)])
        raw_diff = (
            "diff --git a/file.py b/file.py\n"
            "@@ -1,20 +1,21 @@\n"
            + long_context +
            "+ new_addition()\n"
        )
        sliced, orig_t, opt_t = DiffSlicer.slice_diff(raw_diff, max_context_lines=2)
        self.assertIn("context lines omitted", sliced)
        self.assertIn("new_addition()", sliced)
        self.assertLess(opt_t, orig_t)

    def test_optimize_git_diff_command(self):
        cmd = "git diff main"
        opt, saved, reason = DiffSlicer.optimize_git_diff_command(cmd)
        self.assertIsNotNone(opt)
        self.assertIn("-U2", opt)
        self.assertIn("':!*lock*'", opt)
        self.assertGreater(saved, 0)

if __name__ == "__main__":
    unittest.main()
