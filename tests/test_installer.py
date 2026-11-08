import unittest
import os
import json
import tempfile
import shutil
from src.installer import AgentInstaller, AGENTS_REGISTRY

class TestAgentInstaller(unittest.TestCase):
    def test_detection_runs_without_exceptions(self):
        detected = AgentInstaller.detect_available_agents()
        self.assertIn("antigravity", detected)
        self.assertIn("claude_desktop", detected)
        self.assertIn("cursor", detected)
        self.assertIn("windsurf", detected)
        self.assertIn("cline", detected)
        self.assertIn("codex", detected)
        self.assertIn("zcode", detected)

    def test_codex_toml_installation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config = os.path.join(tmpdir, "config.toml")
            fake_skills = os.path.join(tmpdir, "skills", "token-optimizer")
            with open(fake_config, "w", encoding="utf-8") as f:
                f.write('model = "gpt-5.6-luna"\n\n[features]\nmulti_agent = true\n')

            orig_entry = AGENTS_REGISTRY["codex"]
            AGENTS_REGISTRY["codex"] = {
                "name": "OpenAI Codex",
                "type": "codex_toml",
                "config_path": fake_config,
                "skills_path": fake_skills,
                "detect_path": tmpdir
            }
            try:
                ok, msg = AgentInstaller.install_mcp_config("codex")
                self.assertTrue(ok)
                with open(fake_config, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("[mcp_servers.tokensafe]", content)
                self.assertIn("mcp_server.py", content)
                self.assertTrue(os.path.exists(fake_skills))
            finally:
                AGENTS_REGISTRY["codex"] = orig_entry

    def test_cursor_env_installation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config = os.path.join(tmpdir, "mcp.json")
            fake_skills = os.path.join(tmpdir, "skills", "token-optimizer")
            fake_rules = os.path.join(tmpdir, "rules", "tokensafe.mdc")
            fake_ext = os.path.join(tmpdir, "extensions", "tokensafe-1.0.0")

            orig_entry = AGENTS_REGISTRY["cursor"]
            AGENTS_REGISTRY["cursor"] = {
                "name": "Cursor",
                "type": "cursor_env",
                "config_path": fake_config,
                "skills_path": fake_skills,
                "rules_path": fake_rules,
                "extension_path": fake_ext,
                "detect_path": tmpdir
            }
            try:
                ok, msg = AgentInstaller.install_mcp_config("cursor")
                self.assertTrue(ok)
                with open(fake_config, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("tokensafe", data["mcpServers"])
                self.assertTrue(os.path.exists(fake_skills))
                self.assertTrue(os.path.exists(fake_ext))
                self.assertTrue(os.path.exists(fake_rules))
                with open(fake_rules, "r", encoding="utf-8") as f:
                    rule_content = f.read()
                self.assertIn("TokenSafe Optimization Protocol", rule_content)
            finally:
                AGENTS_REGISTRY["cursor"] = orig_entry

    def test_zcode_json_installation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config = os.path.join(tmpdir, "cli", "config.json")
            fake_skills = os.path.join(tmpdir, "skills", "token-optimizer")
            os.makedirs(os.path.dirname(fake_config), exist_ok=True)
            with open(fake_config, "w", encoding="utf-8") as f:
                json.dump({"mcp": {"servers": {"context7": {"type": "http"}}}}, f)

            orig_entry = AGENTS_REGISTRY["zcode"]
            AGENTS_REGISTRY["zcode"] = {
                "name": "Z.ai Code",
                "type": "zcode_json",
                "config_path": fake_config,
                "skills_path": fake_skills,
                "detect_path": tmpdir
            }
            try:
                ok, msg = AgentInstaller.install_mcp_config("zcode")
                self.assertTrue(ok)
                with open(fake_config, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("tokensafe", data["mcp"]["servers"])
                self.assertIn("mcp_server.py", data["mcp"]["servers"]["tokensafe"]["args"][0])
                self.assertTrue(os.path.exists(fake_skills))
            finally:
                AGENTS_REGISTRY["zcode"] = orig_entry

if __name__ == "__main__":
    unittest.main()
