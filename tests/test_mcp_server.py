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

    @patch("src.license_manager.LicenseManager.check_quota", return_value={"is_quota_exceeded": False})
    def test_sanitize_command_mcp(self, mock_quota):
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

    def test_smart_read_file_dep_mode_signature_smaller_than_full(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            old_db = os.environ.get("TOKENSAFE_DB_PATH")
            os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmpdir, "metrics.db")
            try:
                mod_path = os.path.join(tmpdir, "service.py")
                with open(mod_path, "w", encoding="utf-8") as f:
                    f.write(
                        "import os\nimport sys\nimport json\nimport time\nimport re\nimport math\n\n"
                        "def helper_dep(x):\n" + "    x = x + 1\n" * 20 + "    return x\n\n"
                        "def target_fn(v):\n    return helper_dep(v)\n"
                    )
                res_sig = handle_tool_call("smart_read_file", {
                    "path": mod_path, "symbol": "target_fn", "dep_mode": "signature"
                })
                res_full = handle_tool_call("smart_read_file", {
                    "path": mod_path, "symbol": "target_fn", "dep_mode": "full"
                })
                txt_sig = res_sig["content"][0]["text"]
                txt_full = res_full["content"][0]["text"]
                self.assertIn("def target_fn", txt_sig)
                self.assertIn("def helper_dep", txt_sig)
                self.assertIn("Collapsed 6 imports", txt_sig)
                self.assertLess(len(txt_sig), len(txt_full))
            finally:
                if old_db is None:
                    os.environ.pop("TOKENSAFE_DB_PATH", None)
                else:
                    os.environ["TOKENSAFE_DB_PATH"] = old_db

    def test_zero_config_pointer_and_force_full_over_mcp(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            old_db = os.environ.get("TOKENSAFE_DB_PATH")
            os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmpdir, "metrics.db")
            try:
                target = os.path.join(tmpdir, "big.py")
                with open(target, "w", encoding="utf-8") as f:
                    f.write("def target():\n    return 1\n\n" + "\n".join(f"def extra_{i}(): return {i}" for i in range(40)))
                transcript = os.path.join(tmpdir, "transcript.jsonl")
                with open(transcript, "w", encoding="utf-8") as tf:
                    for _ in range(5):
                        tf.write(json.dumps({"content": "long context message " * 100}) + "\n")
                stdin_data = "\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "claude-code"}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target, "force_full": True}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "compact_conversation", "arguments": {"transcript_path": transcript, "invocation_num": 5}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target}}}),
                ]) + "\n"
                fake_in = io.StringIO(stdin_data)
                fake_out = io.StringIO()
                with patch("sys.stdin", fake_in), patch("sys.stdout", fake_out):
                    run_mcp_server()
                resps = [json.loads(ln) for ln in fake_out.getvalue().strip().splitlines() if ln.strip()]
                by_id = {r["id"]: r["result"] for r in resps}
                t3 = by_id[3]["content"][0]["text"]
                t4 = by_id[4]["content"][0]["text"]
                t5 = by_id[5]["content"][0]["text"]
                t7 = by_id[7]["content"][0]["text"]
                self.assertIn("def target", t3)
                self.assertIn("Pointer Hit", t4)
                self.assertIn("force_full=true", t4)
                self.assertIn("def target", t5)
                self.assertNotIn("Pointer Hit", t5)
                # After compact_conversation increments _EPOCH_GEN, pointer resets automatically
                self.assertIn("def target", t7)
                self.assertNotIn("Pointer Hit", t7)
            finally:
                if old_db is None:
                    os.environ.pop("TOKENSAFE_DB_PATH", None)
                else:
                    os.environ["TOKENSAFE_DB_PATH"] = old_db

    def test_unknown_client_without_tools_list_skips_pointer_safely(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            old_db = os.environ.get("TOKENSAFE_DB_PATH")
            os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tmpdir, "metrics.db")
            try:
                target = os.path.join(tmpdir, "plain.py")
                with open(target, "w", encoding="utf-8") as f:
                    f.write("def target():\n    return 1\n\n" + "\n".join(f"def extra_{i}(): return {i}" for i in range(40)))
                stdin_data = "\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "custom-minimal-bot"}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "smart_read_file", "arguments": {"path": target}}}),
                ]) + "\n"
                fake_in = io.StringIO(stdin_data)
                fake_out = io.StringIO()
                with patch("sys.stdin", fake_in), patch("sys.stdout", fake_out):
                    run_mcp_server()
                resps = {r["id"]: r["result"] for r in (json.loads(ln) for ln in fake_out.getvalue().strip().splitlines() if ln.strip())}
                t3 = resps[3]["content"][0]["text"]
                self.assertIn("def target", t3)
                self.assertNotIn("Pointer Hit", t3)
            finally:
                if old_db is None:
                    os.environ.pop("TOKENSAFE_DB_PATH", None)
                else:
                    os.environ["TOKENSAFE_DB_PATH"] = old_db

    def test_smart_read_file_relative_path_with_active_workspace(self):
        import os
        import tempfile
        from src.paths import save_active_workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "service.py")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("def helper():\n    return 42\n")
            save_active_workspace(tmpdir)
            res = handle_tool_call("smart_read_file", {"path": "service.py", "symbol": "helper"})
            self.assertNotIn("isError", res)
            self.assertIn("def helper", res["content"][0]["text"])

    def test_list_dir_relative_path_with_active_workspace(self):
        import os
        import tempfile
        from src.paths import save_active_workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "file.txt"), "w") as f:
                f.write("hello")
            save_active_workspace(tmpdir)
            res = handle_tool_call("list_dir", {"path": "sub"})
            self.assertNotIn("isError", res)
            self.assertIn("file.txt", res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
