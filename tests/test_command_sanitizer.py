import json
import os
import tempfile
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

    def test_git_grep_sanitization(self):
        cmd = "git -C /some/path grep -rn '5+' frontend/ || true"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertIn("':!bun.lock'", new_cmd)
        self.assertIn("':!*.lock'", new_cmd)
        # Excludes must stay on the git grep stage; the `|| true` no-op tail
        # must be untouched and never receive a bogus mid-compound cap.
        self.assertIn("|| true", new_cmd)
        self.assertNotIn("|| true | head", new_cmd)
        self.assertLess(new_cmd.index(":!*.lock"), new_cmd.index("|| true"))

    def test_piped_grep_places_excludes_before_pipe(self):
        cmd = "grep -rn 'func' . | head -n 30"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertTrue(new_cmd.endswith("| head -n 30"))
        self.assertIn("--exclude='*.lock'", new_cmd)

    def test_ls_command_sanitization(self):
        for cmd in ("ls -la frontend", "ls -lah", "ls -l frontend/src", "ls -al"):
            new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
            self.assertIsNotNone(new_cmd)
            self.assertIn("ls -1 -p", new_cmd)
            self.assertGreater(saved, 300)

        # Unflagged ls is unchanged
        cmd_clean = "ls frontend"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd_clean)
        self.assertIsNone(new_cmd)


class TestTestRunnerWatchGuard(unittest.TestCase):
    """Agents launch JS test runners in the background and block until process
    exit; watch mode or an unclosed jest handle means the process never
    terminates and the agent waits forever. Every test-runner command must be
    forced into run-once mode."""

    def test_yarn_test_with_jest_fingerprint_gets_force_exit_and_ci(self):
        cmd = (
            "yarn test --watchAll=false "
            "src/components/AnomalyDetectionUtility/AnomalyMitigationHistory.test.tsx "
            "src/pages/AnomalyPage/AnomalyMitigationDetails.test.tsx"
        )
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertIsNotNone(new_cmd)
        self.assertTrue(new_cmd.startswith("CI=true "))
        self.assertTrue(new_cmd.endswith("--forceExit"))
        self.assertIn("--watchAll=false", new_cmd)
        self.assertGreater(saved, 0)
        self.assertIn("forceExit", reason)

    def test_yarn_test_bare_watchall_normalized(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test --watchAll")
        self.assertIsNotNone(new_cmd)
        self.assertIn("--watchAll=false", new_cmd)
        self.assertTrue(new_cmd.startswith("CI=true "))
        self.assertTrue(new_cmd.endswith("--forceExit"))

    def test_npx_jest_bare_watch_normalized(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("npx jest --watch src/a.test.ts")
        self.assertIn("--watch=false", new_cmd)
        self.assertNotIn(" --watch ", new_cmd)
        self.assertTrue(new_cmd.startswith("CI=true "))
        self.assertTrue(new_cmd.endswith("--forceExit"))

    def test_react_scripts_test_treated_as_jest(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("react-scripts test --watch")
        self.assertIn("--watch=false", new_cmd)
        self.assertIn("--forceExit", new_cmd)
        self.assertTrue(new_cmd.startswith("CI=true "))

    def test_vitest_rewritten_to_run_and_watch_dropped(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("vitest --watch src/a.test.tsx")
        self.assertEqual(new_cmd, "CI=true vitest run src/a.test.tsx")
        self.assertIn("vitest run", reason)

    def test_bare_vitest_gets_run(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("vitest src/a.test.tsx")
        self.assertEqual(new_cmd, "CI=true vitest run src/a.test.tsx")

    def test_vitest_watch_false_gets_run(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("vitest --watch=false src/a.test.tsx")
        self.assertEqual(new_cmd, "CI=true vitest run --watch=false src/a.test.tsx")

    def test_vitest_run_with_watch_dropped(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("vitest run --watch")
        self.assertEqual(new_cmd, "CI=true vitest run")

    def test_npm_test_args_forwarded_with_separator(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("npm test --watchAll=false a.test.tsx")
        self.assertEqual(new_cmd, "CI=true npm test -- --watchAll=false a.test.tsx --forceExit")

    def test_npm_test_with_separator_left_intact(self):
        cmd = "npm test -- --watchAll=false a.test.tsx"
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        self.assertEqual(new_cmd, "CI=true npm test -- --watchAll=false a.test.tsx --forceExit")

    def test_npm_custom_script_args_forwarded(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("npm run test:e2e a.test.tsx")
        self.assertEqual(new_cmd, "CI=true npm run test:e2e -- a.test.tsx")

    def test_generic_runner_gets_ci_only(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test src/a.test.ts")
        self.assertEqual(new_cmd, "CI=true yarn test src/a.test.ts")
        self.assertIn("unknown test runner", reason)
        self.assertNotIn("--forceExit", new_cmd)

    def test_generic_runner_with_ci_already_set_untouched(self):
        self.assertEqual(CommandSanitizer.sanitize("CI=true yarn test src/a.test.ts"), (None, 0, ""))

    def test_fully_equipped_command_untouched(self):
        cmd = "CI=true yarn test --watchAll=false --forceExit a.test.tsx"
        self.assertEqual(CommandSanitizer.sanitize(cmd), (None, 0, ""))

    def test_ci_true_vitest_run_untouched(self):
        self.assertEqual(CommandSanitizer.sanitize("CI=true vitest run"), (None, 0, ""))

    def test_tokensafe_tests_never_rewritten(self):
        self.assertEqual(CommandSanitizer.sanitize("tokensafe tests"), (None, 0, ""))

    def test_non_test_commands_untouched(self):
        for cmd in ("echo hello", "yarn build", "cat jest.config.json", "ls jest.config"):
            with self.subTest(cmd=cmd):
                self.assertEqual(CommandSanitizer.sanitize(cmd), (None, 0, ""))

    def test_pytest_still_handled_by_its_own_rule(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("pytest tests/test_foo.py")
        self.assertIsNotNone(new_cmd)
        self.assertIn("--tb=short", new_cmd)
        self.assertIn("short traceback", reason)

    def test_compound_command_only_test_stage_rewritten(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("cd app && yarn test --watchAll=false a.test.tsx")
        self.assertTrue(new_cmd.startswith("cd app && CI=true "))
        self.assertTrue(new_cmd.endswith("--forceExit"))

    def test_preexisting_ci_env_prefix_respected(self):
        new_cmd, saved, reason = CommandSanitizer.sanitize("CI=true yarn jest --watch a.test.ts")
        self.assertFalse(new_cmd.startswith("CI=true CI=true"))
        self.assertIn("--watch=false", new_cmd)
        self.assertIn("--forceExit", new_cmd)

    # -- package.json peek (opaque test scripts) ----------------------------------

    def test_package_json_jest_script_unlocks_full_treatment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "react-scripts test"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test src/a.test.tsx", cwd=tmp)
            self.assertEqual(new_cmd, "CI=true yarn test src/a.test.tsx --watch=false --forceExit")
            self.assertIn("forceExit", reason)

    def test_package_json_jest_script_with_baked_watch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "jest --watch"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test", cwd=tmp)
            self.assertEqual(new_cmd, "CI=true yarn test --watch=false --forceExit")

    def test_package_json_vitest_script_stays_ci_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "vitest"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test src/a.test.tsx", cwd=tmp)
            self.assertEqual(new_cmd, "CI=true yarn test src/a.test.tsx")

    def test_package_json_unknown_script_stays_ci_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "mocha --watch"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test src/a.test.tsx", cwd=tmp)
            self.assertEqual(new_cmd, "CI=true yarn test src/a.test.tsx")
            self.assertNotIn("--forceExit", new_cmd)

    def test_missing_package_json_falls_back_to_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_cmd, saved, reason = CommandSanitizer.sanitize("yarn test src/a.test.tsx", cwd=tmp)
            self.assertEqual(new_cmd, "CI=true yarn test src/a.test.tsx")

    def test_cd_prefix_resolves_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "app")
            os.mkdir(app_dir)
            with open(os.path.join(app_dir, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "react-scripts test"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize("cd app && yarn test src/a.test.tsx", cwd=tmp)
            self.assertEqual(
                new_cmd,
                "cd app && CI=true yarn test src/a.test.tsx --watch=false --forceExit",
            )

    def test_quoted_cd_target_resolves_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "my app")
            os.mkdir(app_dir)
            with open(os.path.join(app_dir, "package.json"), "w", encoding="utf-8") as fh:
                json.dump({"scripts": {"test": "react-scripts test"}}, fh)
            new_cmd, saved, reason = CommandSanitizer.sanitize('cd "my app" && yarn test', cwd=tmp)
            self.assertEqual(new_cmd, 'cd "my app" && CI=true yarn test --watch=false --forceExit')


if __name__ == "__main__":
    unittest.main()
