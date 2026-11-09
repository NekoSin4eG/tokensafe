import unittest
import json
from src.mcp_server import handle_tool_call, TOOLS

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

if __name__ == "__main__":
    unittest.main()
