import unittest
import tempfile
import json
import subprocess
import os

class TestOutputGuard(unittest.TestCase):
    def test_write_file_on_existing_large_file_blocked(self):
        # Create a file with 60 lines
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".ts") as f:
            for i in range(1, 65):
                f.write(f"// line {i}\n")
            path = f.name

        payload = {
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": path,
                    "Content": "// rewritten content"
                }
            },
            "conversationId": "test_output_guard_sess"
        }

        proc = subprocess.run(
            ["python3", "hooks/pre_tool_use.py"],
            input=json.dumps(payload),
            text=True,
            capture_output=True
        )
        res = json.loads(proc.stdout)
        self.assertEqual(res["decision"], "deny")
        self.assertIn("Output Guard", res["reason"])
        self.assertIn("edit_file", res["reason"])

        if os.path.exists(path):
            os.remove(path)

    def test_write_file_on_new_file_allowed(self):
        # Non-existing path should be allowed
        non_existent_path = "/tmp/new_file_does_not_exist_xyz.ts"
        payload = {
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": non_existent_path,
                    "Content": "export const a = 1;"
                }
            },
            "conversationId": "test_output_guard_sess"
        }

        proc = subprocess.run(
            ["python3", "hooks/pre_tool_use.py"],
            input=json.dumps(payload),
            text=True,
            capture_output=True
        )
        res = json.loads(proc.stdout)
        self.assertEqual(res["decision"], "allow")

if __name__ == "__main__":
    unittest.main()
