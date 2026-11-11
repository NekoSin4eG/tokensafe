"""
Edge-case coverage for the request/context pipeline (v2.0.x deep dive).

Bugs & gaps this file locks down:
1. DiffSlicer appended lockfile excludes to the END of compound commands —
   `git diff && npm test` delivered pathspec garbage to npm.
2. CommandSanitizer applied only ONE rule per command — `git log && pytest`
   left pytest verbose.
3. Split/join of compound commands must preserve `&&` / `;` / `||` / `|`
   operators and never split inside quotes.
4. New command families: rg, git show, git blame, docker/kubectl logs,
   npm/pip installs, cat-of-lockfile, curl stdout cap.
5. Honesty flip: COMMAND_FILTER / LOOP_GUARD / GUARD_* originals are heuristic
   guesses -> labeled `estimated` (+ retro-label migration).
6. Guards: never-terminating commands (tail -f, watch, docker attach) -> ask;
   git-level TLS bypass -> ask.
7. ToolSlicer symbol search across Go/Rust definitions.
8. LoopDetector alternation is not a loop.
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestCompoundStageRewrites(unittest.TestCase):
    """Per-stage sanitize: every rule runs, operators survive, no cross-stage leaks."""

    def setUp(self):
        from src.command_sanitizer import CommandSanitizer
        self.cs = CommandSanitizer

    def test_compound_gets_every_rule(self):
        new_cmd, saved, reason = self.cs.sanitize("git log && pytest tests/ -x")
        self.assertIsNotNone(new_cmd)
        self.assertIn("git log -n 10", new_cmd)
        self.assertIn("pytest tests/ -x -q --tb=short", new_cmd)
        self.assertIn("&&", new_cmd)
        self.assertGreaterEqual(saved, 1900)

    def test_semicolon_compound_gets_every_rule(self):
        new_cmd, _, _ = self.cs.sanitize("git log --oneline ; pytest")
        self.assertIsNotNone(new_cmd)
        self.assertIn("; pytest -q --tb=short", new_cmd)
        self.assertIn("git log -n 10", new_cmd)

    def test_pipe_stage_preserved(self):
        new_cmd, _, _ = self.cs.sanitize("cd /tmp && ls -la")
        self.assertIsNotNone(new_cmd)
        self.assertEqual(new_cmd, "cd /tmp && ls -1 -p")

    def test_excludes_never_leak_into_next_stage(self):
        # Regression: excludes used to be appended to the tail of the command
        new_cmd, _, _ = self.cs.sanitize("git grep TODO && npm test")
        self.assertIsNotNone(new_cmd)
        self.assertIn(":!*.lock", new_cmd)
        grep_idx = new_cmd.index("grep")
        npm_idx = new_cmd.index("npm test")
        self.assertLess(new_cmd.index(":!*.lock"), npm_idx)
        self.assertFalse(new_cmd[npm_idx:].strip().startswith("npm test '"))
        self.assertIn("&&", new_cmd)
        _ = grep_idx

    def test_operators_survive_roundtrip(self):
        for cmd, expected_ops in [
            ("a && b", ["&&"]),
            ("a ; b", [";"]),
            ("a | b", ["|"]),
            ("a || b", ["||"]),
            ("a && b ; c | d || e", ["&&", ";", "|", "||"]),
        ]:
            pairs = self.cs.split_stages(cmd)
            self.assertEqual([op for op, _ in pairs[1:]], expected_ops, cmd)
            self.assertEqual([s for _, s in pairs], cmd.split()[0::2], cmd)

    def test_split_is_quote_safe(self):
        pairs = self.cs.split_stages('echo "a|b&&c" | grep x')
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0][1], 'echo "a|b&&c"')
        self.assertEqual(pairs[1], ("|", "grep x"))
        pairs2 = self.cs.split_stages("git log --grep='foo|bar' -p")
        self.assertEqual(len(pairs2), 1, "|| inside quotes is not an operator")

    def test_sanitize_is_idempotent_on_compounds(self):
        for cmd in ("git log && pytest", "git grep x src/", "rg p src/", "docker logs c1",
                    "npm install x", "curl http://a.b/c", "cat package-lock.json"):
            once, _, _ = self.cs.sanitize(cmd)
            if once is None:
                continue
            twice, _, _ = self.cs.sanitize(once)
            self.assertIsNone(twice, f"second pass must be a no-op for {cmd!r}, got {twice!r}")


class TestNewCommandRules(unittest.TestCase):
    """Command families agents run daily that previously went through untouched."""

    def setUp(self):
        from src.command_sanitizer import CommandSanitizer
        self.cs = CommandSanitizer

    def test_ripgrep_capped(self):
        new_cmd, saved, _ = self.cs.sanitize("rg TODO src/")
        self.assertEqual(new_cmd, "rg TODO src/ | head -n 30")
        self.assertGreater(saved, 0)

    def test_ripgrep_no_ignore_gets_globs(self):
        new_cmd, _, _ = self.cs.sanitize("rg --no-ignore pattern .")
        self.assertIn("-g '!node_modules'", new_cmd)
        self.assertIn("| head -n 30", new_cmd)

    def test_ripgrep_already_limited_untouched(self):
        for cmd in ("rg -m 5 pattern", "rg --max-count 5 pattern", "rg p src/ | head -n 10"):
            self.assertIsNone(self.cs.sanitize(cmd)[0], cmd)

    def test_git_show_capped_unless_stat(self):
        new_cmd, _, _ = self.cs.sanitize("git show HEAD")
        self.assertEqual(new_cmd, "git show HEAD | head -n 200")
        for cmd in ("git show HEAD --stat", "git show HEAD --name-only", "git show HEAD -s"):
            self.assertIsNone(self.cs.sanitize(cmd)[0], cmd)

    def test_git_blame_capped_unless_L(self):
        new_cmd, _, _ = self.cs.sanitize("git blame src/metrics.py")
        self.assertEqual(new_cmd, "git blame src/metrics.py | head -n 200")
        self.assertIsNone(self.cs.sanitize("git blame -L 10,20 src/metrics.py")[0])

    def test_docker_kubectl_logs_capped(self):
        self.assertEqual(self.cs.sanitize("docker logs mycontainer")[0],
                         "docker logs mycontainer --tail 100")
        self.assertEqual(self.cs.sanitize("kubectl logs deploy/api")[0],
                         "kubectl logs deploy/api --tail=100")
        self.assertIsNone(self.cs.sanitize("docker logs --tail 20 c1")[0])
        self.assertIsNone(self.cs.sanitize("kubectl logs --tail=5 pod")[0])

    def test_npm_install_silenced(self):
        for cmd in ("npm install express", "npm i express", "npm ci", "npm add left-pad"):
            new_cmd, _, _ = self.cs.sanitize(cmd)
            self.assertIsNotNone(new_cmd, cmd)
            self.assertIn("--no-progress --no-fund --no-audit", new_cmd)
        # npm test is not an install command: the test-runner guard gives it
        # run-once mode, the install silencer must not touch it
        self.assertEqual(self.cs.sanitize("npm test")[0], "CI=true npm test")
        self.assertIsNone(self.cs.sanitize("npm install express --no-audit")[0])

    def test_pip_install_quiet(self):
        self.assertEqual(self.cs.sanitize("pip install requests")[0], "pip install requests -q")
        self.assertEqual(self.cs.sanitize("pip3 install requests")[0], "pip3 install requests -q")
        self.assertIsNone(self.cs.sanitize("pip install requests -q")[0])

    def test_cat_of_lockfile_bounded(self):
        new_cmd, _, _ = self.cs.sanitize("cat package-lock.json")
        self.assertEqual(new_cmd, "head -n 120 package-lock.json")
        self.assertIsNone(self.cs.sanitize("cat src/main.py")[0])
        self.assertIsNone(self.cs.sanitize("cat -n package-lock.json")[0], "flagged cat is left alone")

    def test_curl_stdout_capped_only_when_final_and_unredirected(self):
        self.assertEqual(self.cs.sanitize("curl https://api.example.com/v1/data")[0],
                         "curl https://api.example.com/v1/data | head -c 4096")
        self.assertIsNone(self.cs.sanitize("curl -o out.json https://x.com")[0])
        self.assertIsNone(self.cs.sanitize("curl url | wc -c")[0], "downstream limiter present")
        self.assertIsNone(self.cs.sanitize("curl -I https://x.com")[0])

    def test_cap_lands_on_last_stage_only(self):
        # git grep is stage 0: its cap must NOT swallow the trailing npm stage
        new_cmd, _, _ = self.cs.sanitize("git grep TODO -- . && npm test")
        self.assertIsNotNone(new_cmd)
        self.assertFalse(new_cmd.endswith("| head -n 30"), new_cmd)


class TestDiffSlicerPerStage(unittest.TestCase):
    """Regression: excludes landed on the tail of compound commands."""

    def test_excludes_stay_on_diff_stage(self):
        from src.diff_slicer import DiffSlicer
        new_cmd, saved, _ = DiffSlicer.optimize_git_diff_command("git diff && npm test")
        self.assertIsNotNone(new_cmd)
        self.assertIn("git diff -U2", new_cmd)
        self.assertIn(":!*lock*", new_cmd)
        self.assertIn("&& npm test", new_cmd)
        self.assertFalse(new_cmd.rstrip().endswith("npm test ':!*.map'"))
        self.assertGreater(saved, 0)

    def test_existing_pathspecs_not_duplicated(self):
        from src.diff_slicer import DiffSlicer
        self.assertIsNone(
            DiffSlicer.optimize_git_diff_command('git diff -- ":!*.lock"')[0],
            "caller already manages excludes: no second set appended")

    def test_pipe_stage_diff_rewritten(self):
        from src.diff_slicer import DiffSlicer
        new_cmd, _, _ = DiffSlicer.optimize_git_diff_command("git diff | wc -l")
        self.assertIsNotNone(new_cmd)
        self.assertIn("| wc -l", new_cmd)


class TestHookChainedRewrite(unittest.TestCase):
    """E2E through hooks/pre_tool_use.py: diff stage AND non-diff stage both rewritten."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_hook_gap_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()

    def _run_hook(self, cmd):
        payload = json.dumps({
            "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
            "conversationId": "gap_sess",
        })
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "pre_tool_use.py")],
            input=payload, capture_output=True, text=True, timeout=60,
            env={**os.environ},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_compound_diff_and_log_both_rewritten(self):
        resp = self._run_hook("git diff && git log -p")
        self.assertEqual(resp.get("decision"), "allow")
        ow = resp.get("overwrite", {}).get("CommandLine", "")
        self.assertIn("git diff -U2", ow)
        self.assertIn(":!*lock*", ow)
        self.assertIn("git log -n 10", ow)
        self.assertIn("&&", ow)

    def test_quoted_pipe_command_untouched_semantics(self):
        resp = self._run_hook('git log --grep="foo|bar" -p')
        ow = resp.get("overwrite", {}).get("CommandLine", "")
        self.assertIn('-n 10', ow)
        self.assertIn('foo|bar', ow)


class TestHonestyFlipForGuessedOriginals(unittest.TestCase):
    """COMMAND_FILTER / LOOP_GUARD / GUARD_* are counterfactual -> estimated."""

    def test_resolve_measurement_type_map(self):
        from src.metrics import resolve_measurement
        for t in ("COMMAND_FILTER", "LOOP_GUARD", "GUARD_SECRET", "GUARD_DESTRUCTIVE",
                  "GUARD_TLS_VERIFY_BYPASS", "GUARD_JUNK_READ", "GUARD_INFINITE",
                  "GUARD_RAW_CODE_SEARCH"):
            self.assertEqual(resolve_measurement(t), "estimated", t)
        # Real-byte events stay measured
        for t in ("TOOL_READ", "AST_SKELETON", "GRAPH_SKELETON", "SUBAGENT_DEDUP", "OUTPUT_GUARD"):
            self.assertEqual(resolve_measurement(t), "measured", t)

    def test_retro_migration_relabels_command_filter_and_loop_guard(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_retro_gap_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db_path = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_DB_PATH"] = db_path
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        from src.metrics import MetricsTracker

        # Create the real schema, then rewind labels to simulate a legacy DB
        # where guessed originals were (wrongly) recorded as 'measured'.
        tracker = MetricsTracker(db_path)
        tracker.record_event("s", "COMMAND_FILTER", 3500, 50)
        tracker.record_event("s", "LOOP_GUARD", 5000, 100)
        tracker.record_event("s", "TOOL_READ", 900, 300)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE events SET measurement = 'measured'")
        conn.commit()
        conn.close()

        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        s = MetricsTracker(db_path).get_summary()
        # Only TOOL_READ remains measured; guessed originals relabelled estimated
        self.assertEqual(s["total_saved_tokens_measured"], 600)
        self.assertEqual(s["total_saved_tokens_estimated"], 3450 + 4900)


class TestGuardInfiniteAndGitTls(unittest.TestCase):
    """Never-terminating commands and git-level TLS bypass."""

    def test_infinite_commands_ask(self):
        from src.adapter_core import guard_command
        for cmd in ("tail -f app.log", "tail -n 5 -f app.log",
                    "watch -n1 kubectl get pods",
                    "docker logs -f c1", "docker logs --follow c1",
                    "kubectl logs -f pod", "docker attach c1",
                    # vitest defaults to an interactive watcher; only `run` is finite
                    "vitest", "npx vitest", "vitest src/a.test.ts",
                    "jest --watch", "vitest --watch"):
            res = guard_command(cmd, "claude_code", "s")
            self.assertEqual(res["decision"], "ask", cmd)

    def test_terminating_lookalikes_pass(self):
        from src.adapter_core import guard_command
        for cmd in ("tail -n 100 app.log", "docker logs --tail 50 c1",
                    "kubectl logs pod | grep error", "git attach --dry-run x",
                    "vitest run", "npx vitest run", "jest"):
            self.assertEqual(guard_command(cmd, "claude_code", "s")["decision"], "allow", cmd)

    def test_git_level_tls_bypass_ask(self):
        from src.adapter_core import guard_command
        for cmd in ("git -c http.sslVerify=false clone https://x",
                    "git -c https.sslVerify=false fetch origin",
                    "GIT_SSL_NO_VERIFY=1 git pull", "GIT_SSL_NO_VERIFY=true git clone https://x"):
            res = guard_command(cmd, "claude_code", "s")
            self.assertEqual(res["decision"], "ask", cmd)

    def test_normal_git_ops_pass(self):
        from src.adapter_core import guard_command
        for cmd in ("git clone https://x", "git pull", "git -c color.ui=always log"):
            self.assertEqual(guard_command(cmd, "claude_code", "s")["decision"], "allow", cmd)


class TestGuardRawSourceSearch(unittest.TestCase):
    """RAW_CODE_SEARCH deny in adapter_core: recursive grep/rg/find crawls
    over source trees are hard-blocked on EVERY adapter (Claude Code, Cursor,
    Antigravity/ZCode) with a redirect to the tokensafe MCP tools — the
    AGENTS.md 'hard-blocked' promise made literal."""

    def test_recursive_source_search_denied(self):
        from src.adapter_core import guard_command
        for cmd in ("grep -rn 'getUser' src/",
                    "grep -R --include='*.ts' foo frontend/src",
                    "egrep -r 'TODO' lib/",
                    "rg pattern src/hooks",
                    # guard.py policy: rg/find are recursive by construction,
                    # so any invocation over a repo crawl is denied
                    "rg --files .",
                    "find . -name '*.py'",
                    "find src -iname '*.tsx'",
                    "cd app && grep -rn 'x' tests/",
                    "git grep -rn '5+' frontend/ || true"):
            res = guard_command(cmd, "claude_code", "s")
            self.assertEqual(res["decision"], "deny", cmd)
            self.assertIn("scoped_search", res["reason"], cmd)
            self.assertGreater(res["saved_tokens"], 0, cmd)

    def test_safe_searches_still_allowed(self):
        from src.adapter_core import guard_command
        for cmd in ("grep -n 'def x' README.md",
                    "grep -c 'error' notes.txt",
                    "cat server.log | grep error",
                    "kubectl logs pod | grep err",
                    "find . -name '*.png'",
                    "git log --oneline -n 5"):
            self.assertEqual(guard_command(cmd, "claude_code", "s")["decision"], "allow", cmd)

    def test_raw_code_search_event_is_estimated(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_rawgrep_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db_path = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_DB_PATH"] = db_path
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        from src.metrics import MetricsTracker, resolve_measurement
        from src.adapter_core import guard_command
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        self.addCleanup(metrics_mod._INITIALIZED_METRICS_DBS.clear)
        self.assertEqual(resolve_measurement("GUARD_RAW_CODE_SEARCH"), "estimated")
        res = guard_command("grep -rn 'x' src/", "cursor", "rawgrep_sess")
        self.assertEqual(res["decision"], "deny")
        s = MetricsTracker(db_path).get_summary()
        self.assertEqual(s["total_saved_tokens_measured"], 0)
        self.assertGreaterEqual(s["total_saved_tokens_estimated"], res["saved_tokens"])


class TestCliTestsWrapper(unittest.TestCase):
    """`tokensafe tests`: unittest output compacted to names + root causes
    so a full-suite run never floods the context window."""

    GREEN = ("....................\n"
             "----------------------------------------------------------------------\n"
             "Ran 20 tests in 0.421s\n\nOK\n")

    RED = (
        "x.....F..E.......\n"
        "======================================================================\n"
        "FAIL: test_bad_math (test_sample.Case.test_bad_math)\n"
        "----------------------------------------------------------------------\n"
        "Traceback (most recent call last):\n"
        '  File "tests/test_sample.py", line 12, in test_bad_math\n'
        "    self.assertEqual(1 + 1, 3)\n"
        "AssertionError: 2 != 3\n"
        "\n"
        "======================================================================\n"
        "ERROR: test_boom (test_sample.Case.test_boom)\n"
        "----------------------------------------------------------------------\n"
        "Traceback (most recent call last):\n"
        '  File "tests/test_sample.py", line 18, in test_boom\n'
        "    raise ValueError('exploded with a very long ' * 20 + 'tail')\n"
        "ValueError: exploded with a very long exploded with a very long exploded with a very long "
        "exploded with a very long exploded with a very long exploded with a very long exploded with "
        "a very long exploded with a very long exploded with a very long exploded with a very long "
        "exploded with a very long exploded with a very long exploded with a very long exploded with "
        "a very long exploded with a very long exploded with a very long exploded with a very long "
        "exploded with a very long exploded with a very long tail\n"
        "\n"
        "----------------------------------------------------------------------\n"
        "Ran 15 tests in 0.102s\n\n"
        "FAILED (failures=1, errors=1)\n")

    def test_green_summary_is_one_line(self):
        from src.cli import summarize_unittest_output
        out = summarize_unittest_output(self.GREEN)
        self.assertIn("Ran 20 tests", out)
        self.assertIn("all tests passed", out)
        self.assertNotIn("✗", out)

    def test_failure_summary_names_and_causes(self):
        import re
        from src.cli import summarize_unittest_output
        out = summarize_unittest_output(self.RED)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        self.assertIn("Ran 15 tests", plain)
        self.assertIn("FAILED (failures=1, errors=1)", plain)
        self.assertIn("FAIL test_bad_math", plain)
        self.assertIn('File "tests/test_sample.py", line 12', plain)
        self.assertIn("AssertionError: 2 != 3", plain)
        self.assertIn("ERROR test_boom", plain)
        # The Ran/FAILED tail must not leak into a failure's root cause
        self.assertNotIn("      FAILED (failures=1, errors=1)", plain)
        # Long exception messages are truncated to keep the report compact
        self.assertIn("…", plain)
        self.assertLess(len(out), 1500)

    def test_full_traceback_flag_keeps_frames(self):
        from src.cli import summarize_unittest_output
        out = summarize_unittest_output(self.RED, full_traceback=True)
        self.assertIn("Traceback (most recent call last):", out)

    def test_garbage_output_falls_back_to_tail(self):
        from src.cli import summarize_unittest_output
        out = summarize_unittest_output("ImportError: No module named 'broken_thing'\n")
        self.assertIn("ImportError", out)

    def _run_cli_tests(self, files, env_extra=None, cli_args=("tests",)):
        tmp = tempfile.mkdtemp(prefix="tokensafe_cli_tests_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # cli.py resolves PROJECT_ROOT as the PARENT of its own directory, so
        # the cli copy must live at <tmp>/root/src/cli.py: then PROJECT_ROOT
        # is <tmp>/root, `from src...` resolves via the src symlink, and the
        # staged tests/ dir is the discovery target.
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "tests"))
        src_real = os.path.join(PROJECT_ROOT, "src")
        os.makedirs(os.path.join(root, "src"))
        for entry in os.listdir(src_real):
            if entry == "cli.py" or entry == "__pycache__":
                continue
            os.symlink(os.path.join(src_real, entry), os.path.join(root, "src", entry))
        shutil.copy(os.path.join(src_real, "cli.py"), os.path.join(root, "src", "cli.py"))
        for fname, body in files.items():
            with open(os.path.join(root, "tests", fname), "w", encoding="utf-8") as f:
                f.write(body)
        env = {**os.environ, "TOKENSAFE_DATA_DIR": os.path.join(tmp, "data"), "TOKENSAFE_TELEMETRY_DISABLED": "1"}
        if env_extra:
            env.update(env_extra)
        res = subprocess.run(
            [sys.executable, os.path.join(root, "src", "cli.py")] + list(cli_args),
            capture_output=True, text=True, timeout=120, env=env,
        )
        return res

    def test_e2e_green_and_red_runs(self):
        import re
        ok = self._run_cli_tests({"test_ok.py": (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_passes(self):\n"
            "        self.assertTrue(True)\n")})
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("all tests passed", ok.stdout)

        bad = self._run_cli_tests({"test_ok.py": (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_passes(self):\n"
            "        self.assertTrue(True)\n"),
            "test_bad.py": (
            "import unittest\n"
            "class B(unittest.TestCase):\n"
            "    def test_fails(self):\n"
            "        self.assertEqual('a', 'b')\n")})
        self.assertEqual(bad.returncode, 1)
        bad_plain = re.sub(r"\x1b\[[0-9;]*m", "", bad.stdout)
        self.assertIn("✗", bad_plain)
        self.assertIn("FAIL test_fails", bad_plain)
        self.assertIn("AssertionError", bad_plain)

    def test_e2e_pytest_launcher(self):
        """`tokensafe tests pytest` forces -q --tb=short; a stubbed pytest
        module on PYTHONPATH stands in for the real one."""
        fake_pkg = (
            "import sys\n"
            "argv = sys.argv[1:]\n"
            "assert '-q' in argv and '--tb=short' in argv, argv\n"
            "print('1 passed in 0.01s')\n"
            "sys.exit(0)\n")
        res = self._run_cli_tests(
            {"test_ok.py": "def test_x():\n    assert True\n"},
            env_extra={"PYTHONPATH": self._fake_pytest_dir(fake_pkg)},
            cli_args=("tests", "pytest"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("1 passed in 0.01s", res.stdout)
        self.assertIn("(pytest)", res.stdout)

    def _fake_pytest_dir(self, body):
        d = tempfile.mkdtemp(prefix="tokensafe_fake_pytest_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "pytest.py"), "w", encoding="utf-8") as f:
            f.write(body)
        return d


class TestSlicerGoRustSymbols(unittest.TestCase):
    """Symbol windowing finds Go/Rust definitions, not just Python/JS."""

    def _write(self, tmp, name, lines):
        path = os.path.join(tmp, name)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_go_func_symbol_window(self):
        from src.slicer import ToolSlicer
        tmp = tempfile.mkdtemp(prefix="tokensafe_go_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lines = ["package main", "import \"fmt\""]
        lines += [f"var filler{i} = {i}" for i in range(120)]
        lines += ["func HandleRequest(w io.Writer) error {", "return nil", "}"]
        lines += [f"var tail{i} = {i}" for i in range(200)]
        path = self._write(tmp, "server.go", lines)
        _, metrics = ToolSlicer.optimize_read_file(
            {"Path": path}, context_hint="explain HandleRequest")
        self.assertEqual(metrics.get("target_symbol"), "HandleRequest")
        self.assertGreater(metrics["saved_tokens"], 0)

    def test_rust_fn_symbol_window(self):
        from src.slicer import ToolSlicer
        tmp = tempfile.mkdtemp(prefix="tokensafe_rs_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lines = ["use std::io;"]
        lines += [f"const FILLER_{i}: u32 = {i};" for i in range(120)]
        lines += ["pub fn parse_config(cfg: &str) -> Result<(), io::Error> {", "Ok(())", "}"]
        lines += [f"const TAIL_{i}: u32 = {i};" for i in range(200)]
        path = self._write(tmp, "lib.rs", lines)
        _, metrics = ToolSlicer.optimize_read_file(
            {"Path": path}, context_hint="review parse_config")
        self.assertEqual(metrics.get("target_symbol"), "parse_config")
        self.assertGreater(metrics["saved_tokens"], 0)

    def test_symbol_window_clamped_at_eof(self):
        from src.slicer import ToolSlicer
        tmp = tempfile.mkdtemp(prefix="tokensafe_eof_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lines = [f"filler = {i}" for i in range(200)] + ["def only_method():", "pass"]
        path = self._write(tmp, "tail.py", lines)
        _, metrics = ToolSlicer.optimize_read_file(
            {"Path": path}, context_hint="only_method")
        self.assertEqual(metrics.get("target_symbol"), "only_method")
        # found_line is 2 lines from EOF: window = 20 before + rest to EOF (22 lines)
        self.assertEqual(metrics.get("lines_sliced"), 22)
        self.assertGreater(metrics["saved_tokens"], 0)

    def test_small_file_untouched(self):
        from src.slicer import ToolSlicer
        tmp = tempfile.mkdtemp(prefix="tokensafe_small_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = self._write(tmp, "small.py", ["def a():", "pass"] * 20)
        _, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="a")
        self.assertEqual(metrics["saved_tokens"], 0)


class TestGuardInspectCommandTiers(unittest.TestCase):
    """MCP guard_inspect: deny secrets/destruction (adapter core), advisory
    for infinite streams and TLS bypass; inspections write no advisory events."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_ginspect_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self._tmp = tmp
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()

    def _inspect(self, cmd):
        from src import mcp_server
        res = mcp_server.handle_tool_call("guard_inspect", {"command": cmd})
        return json.loads(res["content"][0]["text"])

    def _event_count(self):
        import sqlite3
        conn = sqlite3.connect(os.path.join(self._tmp, "metrics.db"))
        try:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def test_literal_secret_in_command_is_denied(self):
        t = self._inspect('curl -H "Authorization: AKIAIOSFODNN7EXAMPLE123" https://x')
        self.assertFalse(t["passed"])
        entry = next(c for c in t["checks"] if c["check_name"] == "SECRET")
        self.assertTrue(entry["blocked"])
        self.assertEqual(self._event_count(), 1, "deny records GUARD_SECRET (estimated)")

    def test_infinite_stream_is_advisory_not_blocked(self):
        t = self._inspect("tail -f app.log")
        self.assertTrue(t["passed"], "advisory must not flip overall verdict")
        entry = next(c for c in t["checks"] if c["check_name"] == "INFINITE_STREAM")
        self.assertTrue(entry["warning_only"])
        self.assertFalse(entry["blocked"])
        self.assertEqual(self._event_count(), 0, "advisory inspection writes no events")

    def test_git_level_tls_bypass_is_advisory(self):
        t = self._inspect("git -c http.sslVerify=false clone https://x")
        entry = next(c for c in t["checks"] if c["check_name"] == "TLS_VERIFY_BYPASS")
        self.assertTrue(entry["warning_only"])
        self.assertEqual(self._event_count(), 0)

    def test_clean_command_has_no_extra_checks(self):
        t = self._inspect("git status --short")
        self.assertTrue(t["passed"])
        self.assertEqual(len(t["checks"]), 1, "only the base TokenSafeGuard entry")


class TestSymbolWindowCoversWholeDefinition(unittest.TestCase):
    """±20/+80 used to cut long function bodies in half; the slice now extends
    to the real end of the enclosing definition (short ones keep the legacy tail)."""

    def _build(self, lines):
        tmp = tempfile.mkdtemp(prefix="tokensafe_symwin_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "sample")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_python_long_function_fully_covered(self):
        from src.slicer import ToolSlicer
        # def at line 101, body 102..251, tails after
        path = self._build([f"filler = {i}" for i in range(100)] +
                           ["def big_function():"] +
                           [f"    x_{i} = {i}" for i in range(150)] +
                           [f"tail = {i}" for i in range(200)])
        ow, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="big_function")
        self.assertEqual(metrics["target_symbol"], "big_function")
        self.assertEqual(ow["StartLine"], 81)
        self.assertEqual(ow["EndLine"], 251, "window extends past the legacy +80 to the body end")
        self.assertGreater(metrics["saved_tokens"], 0)

    def test_python_short_function_keeps_legacy_tail(self):
        from src.slicer import ToolSlicer
        path = self._build([f"filler = {i}" for i in range(100)] +
                           ["def small():", "    return 1"] +
                           [f"    y_{i} = {i}" for i in range(3)] +
                           [f"tail = {i}" for i in range(300)])
        ow, _ = ToolSlicer.optimize_read_file({"Path": path}, context_hint="small")
        self.assertEqual(ow["EndLine"], 181, "short definition: unchanged +80 tail")

    def test_python_method_ends_before_next_method(self):
        from src.slicer import ToolSlicer
        path = self._build([f"filler = {i}" for i in range(100)] +
                           ["class Repo:", "    def first_method(self):"] +
                           [f"        a_{i} = {i}" for i in range(100)] +
                           ["    def second_method(self):", "        return 2"] +
                           [f"tail = {i}" for i in range(200)])
        ow, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="first_method")
        self.assertEqual(ow["EndLine"], 202, "body ends at line 202")
        self.assertLess(ow["EndLine"], 203, "next method's def is not swallowed")

    def test_js_function_fully_covered_by_brace_balance(self):
        from src.slicer import ToolSlicer
        path = self._build([f"const filler{i} = {i};" for i in range(100)] +
                           ["const bigHandler = () => {"] +
                           [f"    step{i}();" for i in range(150)] +
                           ["};"] +
                           [f"const tail{i} = {i};" for i in range(100)])
        ow, _ = ToolSlicer.optimize_read_file({"Path": path}, context_hint="bigHandler")
        self.assertEqual(ow["EndLine"], 252, "closing brace line included")

    def test_go_multiline_signature_covered(self):
        from src.slicer import ToolSlicer
        path = self._build([f"var filler{i} = {i}" for i in range(100)] +
                           ["func HandleLong(ctx context.Context,", "    w io.Writer,", ") error {"] +
                           [f"    step{i}()" for i in range(120)] +
                           ["}"] +
                           [f"var tail{i} = {i}" for i in range(100)])
        ow, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="HandleLong")
        self.assertEqual(metrics["target_symbol"], "HandleLong")
        self.assertEqual(ow["EndLine"], 224, "opening brace found on signature line 3; body fully covered")

    def test_rust_method_ends_at_own_closing_brace(self):
        from src.slicer import ToolSlicer
        path = self._build([f"const FILLER_{i}: u32 = {i};" for i in range(100)] +
                           ["impl Parser {", "    pub fn parse_config(&self, cfg: &str) -> Result<(), Error> {"] +
                           [f"        let step{i} = {i};" for i in range(130)] +
                           ["    }", "}"] +
                           [f"const TAIL_{i}: u32 = {i};" for i in range(100)])
        ow, _ = ToolSlicer.optimize_read_file({"Path": path}, context_hint="parse_config")
        self.assertEqual(ow["EndLine"], 233, "method's own brace closes the window (impl brace excluded)")

    def test_pathological_definition_capped(self):
        from src.slicer import ToolSlicer
        path = self._build([f"filler = {i}" for i in range(100)] +
                           ["def huge():"] +
                           [f"    x_{i} = {i}" for i in range(600)] +
                           [f"tail = {i}" for i in range(100)])
        ow, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="huge")
        self.assertEqual(ow["EndLine"], 501, "capped at found_line + MAX_SYMBOL_WINDOW (101+400)")
        self.assertGreater(metrics["saved_tokens"], 0)

    def test_unbalanced_brace_from_string_is_safe(self):
        from src.slicer import ToolSlicer
        path = self._build([f"const filler{i} = {i};" for i in range(100)] +
                           ["const tpl = `{" + "x" * 50] +
                           [f"const tail{i} = {i};" for i in range(400)])
        ow, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint="tpl")
        self.assertEqual(metrics["target_symbol"], "tpl")
        self.assertLessEqual(ow["EndLine"], 501, "unbalanced scan terminates at the cap")


class TestLoopDetectorAlternation(unittest.TestCase):
    """Alternating A/B calls are not a stuck loop; TTL prune drops stale sessions."""

    def setUp(self):
        from src.loop_detector import LoopDetector
        self.ld = LoopDetector
        self.sess = f"gap_{tempfile.mktemp()[len(tempfile.gettempdir())+1:]}"
        self.ld.reset_session(self.sess)
        self.addCleanup(self.ld.reset_session, self.sess)

    def test_alternation_is_not_a_loop(self):
        is_loop = False
        for i in range(10):
            args = {"Path": f"/f{i % 2}.py"}
            is_loop, _, _ = self.ld.check_loop(self.sess, "read_file", args)
        self.assertFalse(is_loop, "A/B alternation must never trip the guard")

    def test_identical_triple_trips(self):
        args = {"Path": "/same.py"}
        for _ in range(2):
            self.ld.check_loop(self.sess, "read_file", args)
        is_loop, repeats, msg = self.ld.check_loop(self.sess, "read_file", args)
        self.assertTrue(is_loop)
        self.assertGreaterEqual(repeats, 3)
        self.assertIn("identical", msg)

    def test_prune_drops_stale_sessions(self):
        from src.loop_detector import LoopDetector
        state_path = self.ld.STATE_FILE
        import json as _json
        with self.ld._locked_state():
            state = self.ld._load_state()
            stale_sid = f"stale_{self.sess}"
            state[stale_sid] = {"last_sig": "x", "repeat_count": 1, "tool_name": "t", "updated": 0}
            state[self.sess] = {"last_sig": "y", "repeat_count": 1, "tool_name": "t",
                                "updated": __import__("time").time()}
            with open(state_path, "w") as f:
                _json.dump(state, f)
        self.ld.check_loop(self.sess, "read_file", {"Path": "/z.py"})
        with self.ld._locked_state():
            state = self.ld._load_state()
        self.assertNotIn(stale_sid, state, "48h-idle session pruned")
        self.assertIn(self.sess, state, "fresh session kept")


if __name__ == "__main__":
    unittest.main()
