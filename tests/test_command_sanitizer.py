import unittest
from src.command_sanitizer import CommandSanitizer

class TestCommandSanitizer(unittest.TestCase):
    def test_git_diff_lockfile_exclusion(self):
        cmd = "git diff HEAD~1"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("':!package-lock.json'", new_cmd)
        self.assertIn("':!pnpm-lock.yaml'", new_cmd)
        self.assertGreater(saved, 1000)

    def test_git_diff_already_constrained(self):
        cmd = "git diff -- ':!package-lock.json'"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNone(new_cmd)

    def test_git_log_capping(self):
        cmd = "git log"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("-n 10", new_cmd)

    def test_pytest_quiet_traceback(self):
        cmd = "pytest tests/"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("-q --tb=short", new_cmd)

    def test_find_node_modules_pruning(self):
        cmd = "find . -name '*.ts'"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("-not -path '*/node_modules/*'", new_cmd)

    def test_grep_sanitization(self):
        cmd = "grep -rn 'getUserBalance' ."
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("--exclude-dir=", new_cmd)
        self.assertIn("head -n 30", new_cmd)

if __name__ == "__main__":
    unittest.main()
