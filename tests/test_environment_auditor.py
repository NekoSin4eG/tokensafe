import unittest
import tempfile
import json
import os
from unittest.mock import patch
from src.environment_auditor import EnvironmentAuditor

class TestEnvironmentAuditor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "mcp_config.json")
        self.schemas_dir = os.path.join(self.temp_dir.name, "mcp")
        os.makedirs(self.schemas_dir, exist_ok=True)

        # Create sample config
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({
                "mcpServers": {
                    "test_server": {"command": "echo"},
                    "bloated_server": {"command": "echo"}
                }
            }, f)

        # Create schema dirs
        ts_dir = os.path.join(self.schemas_dir, "test_server")
        os.makedirs(ts_dir, exist_ok=True)
        for i in range(5):
            with open(os.path.join(ts_dir, f"tool_{i}.json"), "w") as f:
                f.write("{}")

        bloat_dir = os.path.join(self.schemas_dir, "bloated_server")
        os.makedirs(bloat_dir, exist_ok=True)
        for i in range(50):
            with open(os.path.join(bloat_dir, f"tool_{i}.json"), "w") as f:
                f.write("{}")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_detects_bloat(self):
        with patch.object(EnvironmentAuditor, "get_config_paths", return_value=[self.config_path]), \
             patch.object(EnvironmentAuditor, "get_mcp_schemas_dir", return_value=self.schemas_dir), \
             patch.object(EnvironmentAuditor, "get_skills_dirs", return_value=[]):
            report = EnvironmentAuditor.audit()
            self.assertTrue(report["has_critical_bloat"])
            self.assertEqual(len(report["bloated_servers"]), 1)
            self.assertEqual(report["bloated_servers"][0]["name"], "bloated_server")
            self.assertEqual(report["bloated_servers"][0]["tool_count"], 50)

    def test_bloat_warning(self):
        with patch.object(EnvironmentAuditor, "get_config_paths", return_value=[self.config_path]), \
             patch.object(EnvironmentAuditor, "get_mcp_schemas_dir", return_value=self.schemas_dir), \
             patch.object(EnvironmentAuditor, "get_skills_dirs", return_value=[]):
            warning = EnvironmentAuditor.get_bloat_warning()
            self.assertIsNotNone(warning)
            self.assertIn("bloated_server", warning)
            self.assertIn("50 инструментов", warning)

    def test_disable_and_enable_mcp(self):
        with patch.object(EnvironmentAuditor, "get_config_paths", return_value=[self.config_path]), \
             patch.object(EnvironmentAuditor, "get_mcp_schemas_dir", return_value=self.schemas_dir):
            # Disable
            res = EnvironmentAuditor.disable_mcp("bloated_server")
            self.assertTrue(res["success"])

            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertNotIn("bloated_server", cfg["mcpServers"])
            self.assertIn("bloated_server", cfg["disabledMcpServers"])
            self.assertTrue(os.path.isdir(os.path.join(self.schemas_dir, "bloated_server.disabled")))

            # Enable back
            res_en = EnvironmentAuditor.enable_mcp("bloated_server")
            self.assertTrue(res_en["success"])

            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg_en = json.load(f)
            self.assertIn("bloated_server", cfg_en["mcpServers"])
            self.assertNotIn("bloated_server", cfg_en.get("disabledMcpServers", {}))
            self.assertTrue(os.path.isdir(os.path.join(self.schemas_dir, "bloated_server")))

if __name__ == "__main__":
    unittest.main()
