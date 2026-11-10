import unittest
import json
import io
import time
from unittest.mock import patch
from src.mcp_server import handle_tool_call, _execute_with_timeout, run_mcp_server, TOOLS

class TestMCPServer(unittest.TestCase):
    def test_tools_list_has_all_schemas(self):
        tool_names = [t["name"] for t in TOOLS]
        self.assertIn("smart_read_file", tool_names)
        self.assertIn("compress_prompt", tool_names)
        self.assertIn("sanitize_command", tool_names)
        self.assertIn("get_token_savings_metrics", tool_names)

    def test_compress_prompt_mcp(self):
        res = handle_tool_call("compress_prompt", {"prompt": "Слушай, перепиши функцию get_balance в wallet.ts"})
        self.assertNotIn("isError", res)
        content = res["content"][0]["text"]
        data = json.loads(content)
        self.assertIn("compact_text", data)

    def test_sanitize_command_mcp(self):
        res = handle_tool_call("sanitize_command", {"command": "git diff HEAD~1"})
        self.assertNotIn("isError", res)
        content = res["content"][0]["text"]
        data = json.loads(content)
        self.assertIn("':!package-lock.json'", data["sanitized_command"])
        self.assertFalse(data.get("blocked", False))

    def test_sanitize_command_guard_blocked_mcp(self):
        res = handle_tool_call("sanitize_command", {"command": "rm -rf /"})
        self.assertNotIn("isError", res)
        content = res["content"][0]["text"]
        data = json.loads(content)
        self.assertTrue(data.get("blocked", False))
        self.assertEqual(data.get("check_name"), "DESTRUCTIVE_RM")

    def test_guard_inspect_command_mcp(self):
        res = handle_tool_call("guard_inspect", {"command": "git add .env"})
        self.assertNotIn("isError", res)
        content = res["content"][0]["text"]
        data = json.loads(content)
        self.assertTrue(data.get("blocked"))
        self.assertFalse(data.get("passed"))
        self.assertEqual(data["checks"][0]["check_name"], "GIT_SECRET_STAGING")

    def test_guard_inspect_code_mcp(self):
        bad_code = 'const key = process.env.JWT_SECRET || "production_secret_token_123456789";'
        res = handle_tool_call("guard_inspect", {"code": bad_code, "file_path": "src/auth.ts"})
        self.assertNotIn("isError", res)
        content = res["content"][0]["text"]
        data = json.loads(content)
        self.assertTrue(data.get("blocked"))
        self.assertEqual(data["checks"][0]["check_name"], "SECRET_FALLBACK")

    def test_list_dir_mcp(self):
        res = handle_tool_call("list_dir", {"path": "src", "depth": 2})
        self.assertNotIn("isError", res)
        text = res["content"][0]["text"]
        self.assertIn("📁 **src/**", text)
        self.assertIn("skeletonizer.py", text)
        self.assertIn("command_sanitizer.py", text)

    def test_tool_exception_returns_fallback_guidance(self):
        with patch("src.mcp_server._dispatch_tool_call", side_effect=RuntimeError("boom")):
            res = handle_tool_call("list_dir", {"path": "src"})
        self.assertTrue(res.get("isError"))
        text = res["content"][0]["text"]
        self.assertIn("Try an alternative approach", text)
        self.assertIn("RuntimeError: boom", text)

    def test_execute_with_timeout_recovers_without_hanging(self):
        def _slow_handler(*args, **kwargs):
            time.sleep(0.5)
            return {"content": [{"type": "text", "text": "ok"}]}

        with patch("src.mcp_server.handle_tool_call", side_effect=_slow_handler):
            res = _execute_with_timeout("scoped_search", {"query": "test"}, timeout=0.05)
        self.assertTrue(res.get("isError"))
        self.assertIn("timeout", res["content"][0]["text"])
        self.assertIn("Try an alternative approach", res["content"][0]["text"])

    def test_notifications_cancelled_ignored_without_error_response(self):
        stdin_data = (
            json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list"}) + "\n"
        )
        fake_in = io.StringIO(stdin_data)
        fake_out = io.StringIO()
        with patch("sys.stdin", fake_in), patch("sys.stdout", fake_out):
            run_mcp_server()
        lines = [line for line in fake_out.getvalue().strip().splitlines() if line]
        self.assertEqual(len(lines), 1)
        resp = json.loads(lines[0])
        self.assertEqual(resp["id"], 99)
        self.assertIn("tools", resp["result"])

    def test_run_mcp_server_exits_cleanly_after_disk_upgrade(self):
        # Two tools/call requests queued, but watched mtimes change after the 1st request
        stdin_data = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_dir", "arguments": {"path": "src"}}}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_dir", "arguments": {"path": "src"}}}) + "\n"
        )
        fake_in = io.StringIO(stdin_data)
        fake_out = io.StringIO()
        mtimes_seq = [(100.0, 100.0, None), (200.0, 100.0, None)]

        with patch("sys.stdin", fake_in), \
             patch("sys.stdout", fake_out), \
             patch("src.mcp_server._get_watched_mtimes", side_effect=mtimes_seq):
            run_mcp_server()

        lines = [line for line in fake_out.getvalue().strip().splitlines() if line]
        # Only the 1st request is answered; server then exits cleanly so host IDE respawns new code
        self.assertEqual(len(lines), 1)
        resp = json.loads(lines[0])
        self.assertEqual(resp["id"], 1)
        self.assertFalse(resp["result"].get("isError", False))

if __name__ == "__main__":
    unittest.main()
