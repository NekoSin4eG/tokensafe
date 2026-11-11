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

    def test_edit_file_reaches_guard_branch(self):
        """hooks.json matcher now includes edit_file; the guard branch
        (pre_tool_use.py: write/edit tuple check) must fire for it."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import io
        from hooks.pre_tool_use import main as hook_main
        from src.guard import TokenSafeGuard

        import uuid
        payload = {
            "toolCall": {"name": "edit_file", "args": {"Path": "a.py", "Replacement": "x = 1"}},
            # Unique per run: the shared persistent metrics DB would otherwise
            # trip the Loop Guard on the second test-suite run (identical call).
            "conversationId": f"guard_edit_{uuid.uuid4().hex[:8]}",
        }
        guard_res = SimpleNamespace(blocked=True, check_name="SECRET_FALLBACK", reason="boom")
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_out, \
             patch.object(TokenSafeGuard, "inspect_file_write", staticmethod(lambda p, c: guard_res)):
            hook_main()
            res = json.loads(mock_out.getvalue())
        self.assertEqual(res["decision"], "deny")
        self.assertIn("TokenSafe Guard", res["reason"])

    def test_read_file_unmodified_when_quota_exceeded(self):
        # Create a file with 100 lines
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".py") as f:
            for i in range(1, 105):
                f.write(f"def func_{i}():\n    return {i}\n")
            path = f.name

        payload = {
            "toolCall": {
                "name": "read_file",
                "args": {
                    "Path": path
                }
            },
            "conversationId": "test_quota_tool_sess"
        }

        # Run with patched check_quota in environment or mock
        script = f"""
import json, sys
from unittest.mock import patch
from src.license_manager import LicenseManager

with patch.object(LicenseManager, 'check_quota', return_value={{'is_quota_exceeded': True, 'tier': 'free'}}):
    import subprocess
    p = subprocess.run(['python3', 'hooks/pre_tool_use.py'], input='''{json.dumps(payload)}''', text=True, capture_output=True)
    sys.stdout.write(p.stdout)
"""
        # Alternatively call hooks/pre_tool_use.py main() directly in Python
        from unittest.mock import patch
        import io
        from hooks.pre_tool_use import main as hook_main

        with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_out, \
             patch("src.license_manager.LicenseManager.check_quota", return_value={"is_quota_exceeded": True, "tier": "free"}):
            hook_main()
            res = json.loads(mock_out.getvalue())
            self.assertEqual(res.get("decision"), "allow")
            self.assertNotIn("overwrite", res)

        if os.path.exists(path):
            os.remove(path)

if __name__ == "__main__":
    unittest.main()
