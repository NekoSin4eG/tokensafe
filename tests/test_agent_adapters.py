"""
Cross-agent lifecycle-hook adapters (Claude Code / Cursor) — JSON contract tests.

Contracts verified against official docs (Nov 2026):
- Claude Code UserPromptSubmit: hookSpecificOutput.additionalContext injection
- Claude Code PreToolUse: permissionDecision allow/deny/ask
- Cursor beforeShellExecution/beforeReadFile: permission allow/deny/ask
- Cursor BLOCKS on invalid JSON => every failure path must print valid allow-JSON
"""

import os
import sys
import json
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.adapter_core import compact_for_injection, guard_command, guard_read

VERBOSE_PROMPT = (
    "Now I want you to carefully review and refactor the entire authentication module "
    "in the src/auth directory to use async handlers instead of blocking calls because "
    "the current implementation blocks the event loop and causes request timeouts"
)


class AdapterBase(unittest.TestCase):
    def setUp(self):
        import importlib
        from src import metrics as metrics_mod
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_adapters_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)

    def _summary(self):
        from src.metrics import MetricsTracker
        return MetricsTracker().get_summary()


class TestClaudeCodeAdapter(AdapterBase):
    def _run_hook(self, payload: dict) -> dict:
        import subprocess
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "claude_code_hook.py")],
            input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout or "{}")

    def test_user_prompt_submit_injects_tocp_context(self):
        out = self._run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": VERBOSE_PROMPT,
            "session_id": "cc_test_session",
        })
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[TOCP Compressed Intent]", ctx)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        # Savings recorded with honest estimated label (injection, not replace)
        s = self._summary()
        self.assertEqual(s["breakdown"].get("PROMPT", {}).get("count", 0), 1)
        self.assertGreater(s["total_saved_tokens_estimated"], 0)

    def test_session_start_injects_directives_once(self):
        out = self._run_hook({
            "hook_event_name": "SessionStart",
            "session_id": "cc_test_session",
            "source": "startup",
        })
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Mandatory Directives", out["hookSpecificOutput"]["additionalContext"])
        # One-shot injection records no metrics on its own
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"].count("tokensafe:"), 4)

    def test_short_prompt_passes_through_clean(self):
        out = self._run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "fix bug",
            "session_id": "cc_test_session",
        })
        self.assertEqual(out, {})

    def test_pretooluse_denies_secret_command(self):
        out = self._run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE aws s3 ls"},
            "session_id": "cc_test_session",
        })
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_pretooluse_denies_destructive_command(self):
        out = self._run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf / --no-preserve-root"},
            "session_id": "cc_test_session",
        })
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_pretooluse_asks_on_tls_bypass(self):
        out = self._run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "curl -k https://internal.example.com/health"},
            "session_id": "cc_test_session",
        })
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_pretooluse_allows_normal_command(self):
        out = self._run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/ -q"},
            "session_id": "cc_test_session",
        })
        self.assertEqual(out, {})

    def test_fail_open_on_garbage(self):
        import subprocess
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "claude_code_hook.py")],
            input="not-json{{", capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout or "{}"), {})


class TestMcpClientAttribution(AdapterBase):
    """MCP initialize clientInfo routes session-scoped metrics to the REAL agent."""

    def _run_server(self, messages):
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "src", "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ}
        )
        out, _ = proc.communicate("\n".join(json.dumps(m) for m in messages) + "\n", timeout=30)
        return [json.loads(l) for l in out.strip().split("\n") if l.strip()]

    def test_events_attributed_to_declared_client(self):
        import sqlite3
        demo = os.path.join(self._tmp, "demo.py")
        with open(demo, "w") as f:
            f.write("def handler(x):\n" + "    y = x * 2\n" * 25 + "    return y\n")
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "ZCode"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "smart_read_file", "arguments": {"path": demo, "symbol": "handler"}}},
        ]
        self._run_server(msgs)
        conn = sqlite3.connect(os.environ["TOKENSAFE_DB_PATH"])
        rows = conn.execute("SELECT DISTINCT session_id FROM events").fetchall()
        conn.close()
        self.assertTrue(any(r[0] == "mcp:zcode" for r in rows), f"expected mcp:zcode, got {rows}")

    def test_no_initialize_falls_back_to_mcp_session(self):
        import sqlite3
        demo = os.path.join(self._tmp, "demo2.py")
        with open(demo, "w") as f:
            f.write("def beta():\n    return 1\n")
        msgs = [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "smart_read_file", "arguments": {"path": demo, "symbol": "beta"}}},
        ]
        self._run_server(msgs)
        conn = sqlite3.connect(os.environ["TOKENSAFE_DB_PATH"])
        rows = conn.execute("SELECT DISTINCT session_id FROM events").fetchall()
        conn.close()
        self.assertTrue(any(r[0] == "mcp_session" for r in rows), f"expected mcp_session fallback, got {rows}")


class TestCursorAdapter(AdapterBase):
    def _run_hook(self, payload: dict) -> dict:
        import subprocess
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "cursor_hook.py")],
            input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout or "null")
        self.assertIsInstance(out, dict)  # Cursor blocks on invalid JSON
        return out

    def test_shell_guard_denies_secret(self):
        out = self._run_hook({
            "hook_event_name": "beforeShellExecution",
            "command": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE aws s3 ls",
            "conversation_id": "cur_test",
        })
        self.assertEqual(out["permission"], "deny")
        self.assertIn("secret", out["user_message"].lower())

    def test_shell_guard_allows_normal(self):
        out = self._run_hook({
            "hook_event_name": "beforeShellExecution",
            "command": "npm test",
            "conversation_id": "cur_test",
        })
        self.assertEqual(out, {"permission": "allow"})

    def test_read_guard_denies_lockfile(self):
        out = self._run_hook({
            "hook_event_name": "beforeReadFile",
            "file_path": "/repo/frontend/bun.lock",
            "conversation_id": "cur_test",
        })
        self.assertEqual(out["permission"], "deny")
        self.assertIn("scoped_search", out["user_message"])

    def test_read_guard_allows_source(self):
        out = self._run_hook({
            "hook_event_name": "beforeReadFile",
            "file_path": "/repo/src/main.ts",
            "conversation_id": "cur_test",
        })
        self.assertEqual(out, {"permission": "allow"})

    def test_prompt_hook_is_honest_noop(self):
        # Cursor cannot inject: adapter must allow and record nothing.
        out = self._run_hook({
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": VERBOSE_PROMPT,
            "conversation_id": "cur_test",
        })
        self.assertEqual(out, {"continue": True})
        self.assertEqual(self._summary()["breakdown"].get("PROMPT", {}).get("count", 0), 0)

    def test_fail_open_emits_valid_allow_json(self):
        import subprocess
        res = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "hooks", "cursor_hook.py")],
            input="garbage{{{", capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIsInstance(json.loads(res.stdout), dict)  # never blocks the agent


class TestAdapterCoreGuards(unittest.TestCase):
    def test_guard_command_saved_tokens_estimated(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_core_guards_")
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        v = guard_command("curl -k https://x.example.com", "claude_code", "s1")
        self.assertEqual(v["decision"], "ask")
        s = self._summary_local(tmp)
        self.assertEqual(s["breakdown"].get("GUARD_TLS_VERIFY_BYPASS", {}).get("count", 0), 1)
        # Blocked-command mass is a heuristic guess (len/4), never metered I/O:
        # honest label is estimated, and measured must stay untouched.
        self.assertEqual(s["total_saved_tokens_measured"], 0)
        self.assertGreater(s["total_saved_tokens_estimated"], 0)

    @staticmethod
    def _summary_local(db_dir):
        from src.metrics import MetricsTracker
        return MetricsTracker(os.path.join(db_dir, "metrics.db")).get_summary()

    def test_junk_read_patterns(self):
        for path, denied in [
            ("/r/bun.lock", True),
            ("/r/pkg/package-lock.json", True),
            ("/r/dist/app.min.js", True),
            ("/r/node_modules/x/index.js", True),
            ("/r/src/main.ts", False),
            ("/r/README.md", False),
        ]:
            self.assertEqual(guard_read(path, "cursor")["decision"],
                             "deny" if denied else "allow", path)

    def test_injection_labels_estimated(self):
        tmp = tempfile.mkdtemp(prefix="tokensafe_core_inj_")
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        ctx, meta = compact_for_injection(VERBOSE_PROMPT, "claude_code", "s2")
        self.assertTrue(meta["compacted"])
        self.assertIn("[TOCP Compressed Intent]", ctx)
        from src.metrics import MetricsTracker
        s = MetricsTracker().get_summary()
        self.assertEqual(s["breakdown"]["PROMPT"]["count"], 1)
        self.assertEqual(s["breakdown"]["PROMPT"]["estimated_saved"], s["breakdown"]["PROMPT"]["saved"])


if __name__ == "__main__":
    unittest.main()
