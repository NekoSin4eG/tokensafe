import unittest
import tempfile
import json
import os
from unittest.mock import patch
from src.environment_auditor import EnvironmentAuditor, RULE_FILE_LIMIT_BYTES

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
            self.assertIn("50 tools", warning)

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

class TestAuditorWorkspaceAndRules(unittest.TestCase):
    """rules.md contract: 24 KB per rule file (after @[label](path) expansion),
    20,000-token shared always-on budget; workspace `.agents/` counts as a
    skills root."""

    def test_get_skills_dirs_includes_workspace_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".agents"))
            dirs = EnvironmentAuditor.get_skills_dirs(workspace_root=tmp)
            self.assertIn(os.path.join(tmp, ".agents"), dirs)

    def test_rules_audit_include_expansion_and_per_file_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "inc.md"), "w", encoding="utf-8") as f:
                f.write("B" * 500)
            agents = os.path.join(tmp, "AGENTS.md")
            with open(agents, "w", encoding="utf-8") as f:
                f.write("A" * 300 + "\n@[docs](inc.md)\n")

            rep = EnvironmentAuditor.audit_rules(workspace_root=tmp)
            self.assertEqual(rep["files"], 1)
            self.assertEqual(rep["violations"], [])
            # effective text = 300 A's + 500 B's; raw file is only ~320 bytes
            self.assertGreater(rep["total_tokens"], 100)

            with open(agents, "w", encoding="utf-8") as f:
                f.write("A" * (RULE_FILE_LIMIT_BYTES + 500))
            rep2 = EnvironmentAuditor.audit_rules(workspace_root=tmp)
            self.assertEqual(len(rep2["violations"]), 1)
            self.assertEqual(rep2["violations"][0]["path"], "AGENTS.md")
            self.assertGreater(rep2["violations"][0]["effective_bytes"], RULE_FILE_LIMIT_BYTES)

    def test_rules_budget_exceeded_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("word " * 500)
            with patch("src.environment_auditor.RULES_BUDGET_TOKENS", 10):
                rep = EnvironmentAuditor.audit_rules(workspace_root=tmp)
            self.assertTrue(rep["budget_exceeded"])

    def test_bloat_warning_mentions_rule_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("A" * (RULE_FILE_LIMIT_BYTES + 10))
            with patch.object(EnvironmentAuditor, "get_config_paths", return_value=[]), \
                 patch.object(EnvironmentAuditor, "get_mcp_schemas_dir", return_value=os.path.join(tmp, "mcp")):
                warning = EnvironmentAuditor.get_bloat_warning(workspace_root=tmp)
            self.assertIsNotNone(warning)
            self.assertIn("AGENTS.md", warning)
            self.assertIn("byte", warning)

if __name__ == "__main__":
    unittest.main()
