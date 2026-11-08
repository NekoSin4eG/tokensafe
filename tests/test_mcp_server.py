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

if __name__ == "__main__":
    unittest.main()
