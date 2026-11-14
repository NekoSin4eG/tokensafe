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


class TestDiffSlicerPassThrough(unittest.TestCase):
    def test_pass_through_when_slicing_earns_nothing(self):
        # QA sweep finding: tiny diffs came out BIGGER after slicing (omission
        # annotations cost more than the context they save) -> negative savings.
        # DiffSlicer must return the raw diff unchanged in that case.
        tiny_diff = (
            "diff --git a/x.py b/x.py\n"
            "index 111..222 100644\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,3 +1,4 @@\n"
            " context line one\n"
            " context line two\n"
            "+added line"
        )
        sliced, orig_t, compact_t = DiffSlicer.slice_diff(tiny_diff)
        self.assertEqual(sliced, tiny_diff)
        self.assertEqual(orig_t, compact_t)

    def test_pass_through_on_forced_expansion(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            " ctx1\n"
            " ctx2\n"
            "+plus\n"
            " ctx3\n"
        )
        sliced, orig_t, compact_t = DiffSlicer.slice_diff(diff, max_context_lines=0)
        self.assertEqual(sliced, diff)
        self.assertEqual(compact_t, orig_t)
