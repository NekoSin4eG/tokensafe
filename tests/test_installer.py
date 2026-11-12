import unittest
from unittest.mock import patch, MagicMock
import os
import json
import signal
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
                self.assertIn("TokenSafe", rule_content)
                self.assertIn("get_architecture_graph", rule_content)
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

    def test_is_tokensafe_mcp_command_filtering(self):
        self.assertTrue(AgentInstaller._is_tokensafe_mcp_command("python3 /app/bin/tokensafe mcp"))
        self.assertTrue(AgentInstaller._is_tokensafe_mcp_command("node /usr/local/bin/tokensafe mcp"))
        self.assertTrue(AgentInstaller._is_tokensafe_mcp_command("python3 /Users/dev/agy-token-optimizer/src/mcp_server.py"))
        self.assertFalse(AgentInstaller._is_tokensafe_mcp_command("python3 /app/bin/tokensafe update"))
        self.assertFalse(AgentInstaller._is_tokensafe_mcp_command("python3 /app/bin/tokensafe mcp list"))
        self.assertFalse(AgentInstaller._is_tokensafe_mcp_command("zsh -c 'ps aux | grep tokensafe mcp'"))
        self.assertFalse(AgentInstaller._is_tokensafe_mcp_command("python3 -c 'print(\"tokensafe mcp\")'"))

    def test_restart_mcp_daemons_signals_matching_pids_only(self):
        fake_ps = "\n".join([
            f"{os.getpid()} python3 /app/bin/tokensafe update",
            "54321 python3 /Users/dev/agy-token-optimizer/bin/tokensafe mcp",
            "54322 node /Users/dev/.nvm/bin/tokensafe mcp",
            "54323 python3 /Users/dev/agy-token-optimizer/bin/tokensafe mcp list",
            "54324 zsh -c ps aux | python3 -c tokensafe mcp",
        ])
        mock_proc = MagicMock(returncode=0, stdout=fake_ps)
        killed = []

        def fake_kill(pid, sig):
            if sig == 0:
                raise ProcessLookupError()
            killed.append((pid, sig))

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"TOKENSAFE_DATA_DIR": os.path.join(tmpdir, "data")}), \
             patch("src.installer.subprocess.run", return_value=mock_proc), \
             patch("src.installer.os.kill", side_effect=fake_kill), \
             patch("src.installer.time.sleep"):
            count = AgentInstaller.restart_mcp_daemons()
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "data", ".mcp_reload")))

        self.assertEqual(count, 2)
        self.assertEqual(killed, [(54321, signal.SIGTERM), (54322, signal.SIGTERM)])


class TestAntigravityPluginInstall(unittest.TestCase):
    """Antigravity keys plugin enable-state by directory name (plugins.md):
    a canonical symlink must be created and any stale legacy-named symlink
    removed — otherwise every hook runs twice per turn."""

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_canonical_symlink_created_and_legacy_symlink_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins = os.path.join(tmpdir, "plugins")
            os.makedirs(plugins)
            target = os.path.join(plugins, "tokensafe")
            legacy = os.path.join(plugins, "agy-token-optimizer")
            os.symlink(self.repo_root, legacy)  # stale legacy symlink

            orig_entry = AGENTS_REGISTRY["antigravity"]
            AGENTS_REGISTRY["antigravity"] = {
                "name": "Google Antigravity",
                "type": "plugin",
                "target_path": target,
                "legacy_path": legacy,
                "detect_path": tmpdir,
            }
            try:
                # PROJECT_ROOT patched: the tmpdir has no mcp_config.json, so the
                # user-level MCP migration (a real-config side effect) must not run.
                with patch("src.installer.PROJECT_ROOT", tmpdir):
                    ok, msg = AgentInstaller.install_mcp_config("antigravity")
                self.assertTrue(ok)
                self.assertTrue(os.path.islink(target))
                self.assertEqual(os.path.realpath(target), os.path.realpath(tmpdir))
                self.assertFalse(os.path.lexists(legacy), "stale legacy symlink must be removed")
            finally:
                AGENTS_REGISTRY["antigravity"] = orig_entry
                if os.path.islink(target):
                    os.unlink(target)

    def test_real_legacy_directory_is_not_touched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins = os.path.join(tmpdir, "plugins")
            os.makedirs(plugins)
            target = os.path.join(plugins, "tokensafe")
            legacy = os.path.join(plugins, "agy-token-optimizer")
            os.makedirs(legacy)
            sentinel = os.path.join(legacy, "user-data.txt")
            with open(sentinel, "w", encoding="utf-8") as f:
                f.write("keep me")

            orig_entry = AGENTS_REGISTRY["antigravity"]
            AGENTS_REGISTRY["antigravity"] = {
                "name": "Google Antigravity",
                "type": "plugin",
                "target_path": target,
                "legacy_path": legacy,
                "detect_path": tmpdir,
            }
            try:
                with patch("src.installer.PROJECT_ROOT", tmpdir):
                    ok, msg = AgentInstaller.install_mcp_config("antigravity")
                self.assertTrue(ok)
                self.assertTrue(os.path.isdir(legacy))
                self.assertTrue(os.path.isfile(sentinel))
                self.assertTrue(os.path.islink(target))
            finally:
                AGENTS_REGISTRY["antigravity"] = orig_entry
                if os.path.islink(target):
                    os.unlink(target)

    def test_install_without_legacy_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins = os.path.join(tmpdir, "plugins")
            os.makedirs(plugins)
            target = os.path.join(plugins, "tokensafe")

            orig_entry = AGENTS_REGISTRY["antigravity"]
            AGENTS_REGISTRY["antigravity"] = {
                "name": "Google Antigravity",
                "type": "plugin",
                "target_path": target,
                "detect_path": tmpdir,
            }
            try:
                with patch("src.installer.PROJECT_ROOT", tmpdir):
                    ok, msg = AgentInstaller.install_mcp_config("antigravity")
                self.assertTrue(ok)
                self.assertTrue(os.path.islink(target))
            finally:
                AGENTS_REGISTRY["antigravity"] = orig_entry
                if os.path.islink(target):
                    os.unlink(target)


class TestUserLevelMcpMigration(unittest.TestCase):
    """Once the plugin declares its own mcp_config.json, leftover user-level
    tokensafe MCP entries in Antigravity's configs would register the server
    twice — the installer migrates them away (with a backup)."""

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _fake_home_and_repo(self, tmpdir, plugin_declares_mcp):
        home = os.path.join(tmpdir, "home")
        config_dir = os.path.join(home, ".gemini", "config")
        os.makedirs(config_dir)
        cfg = os.path.join(config_dir, "mcp_config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({
                "mcpServers": {
                    "tokensafe": {"command": "node", "args": ["tokensafe", "mcp"]},
                    "other-server": {"command": "echo"},
                }
            }, f)

        fake_repo = os.path.join(tmpdir, "repo")
        os.makedirs(fake_repo)
        if plugin_declares_mcp:
            with open(os.path.join(fake_repo, "mcp_config.json"), "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"tokensafe": {"command": "sh"}}}, f)
        return home, cfg, fake_repo

    def _install_antigravity(self, tmpdir, fake_repo):
        plugins = os.path.join(tmpdir, "plugins")
        target = os.path.join(plugins, "tokensafe")
        orig_entry = AGENTS_REGISTRY["antigravity"]
        AGENTS_REGISTRY["antigravity"] = {
            "name": "Google Antigravity",
            "type": "plugin",
            "target_path": target,
            "detect_path": tmpdir,
        }
        try:
            with patch("src.installer.PROJECT_ROOT", fake_repo), \
                 patch("src.installer.HOME", os.path.join(tmpdir, "home")):
                ok, msg = AgentInstaller.install_mcp_config("antigravity")
        finally:
            AGENTS_REGISTRY["antigravity"] = orig_entry
            if os.path.islink(target):
                os.unlink(target)
        return ok, msg

    def test_removes_user_level_entry_with_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home, cfg, fake_repo = self._fake_home_and_repo(tmpdir, plugin_declares_mcp=True)
            ok, msg = self._install_antigravity(tmpdir, fake_repo)
            self.assertTrue(ok)
            self.assertIn("migrated", msg)
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertNotIn("tokensafe", data["mcpServers"])
            self.assertIn("other-server", data["mcpServers"])
            self.assertTrue(os.path.exists(cfg + ".backup"))

    def test_noop_when_plugin_does_not_declare_mcp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home, cfg, fake_repo = self._fake_home_and_repo(tmpdir, plugin_declares_mcp=False)
            ok, msg = self._install_antigravity(tmpdir, fake_repo)
            self.assertTrue(ok)
            self.assertNotIn("migrated", msg)
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("tokensafe", data["mcpServers"])
            self.assertFalse(os.path.exists(cfg + ".backup"))


class TestAgentsPluginsConfig(unittest.TestCase):
    """install_project(agents_plugin_path=...) writes/merges .agents/plugins.json
    (json_configs.md) for team sharing on Antigravity."""

    def test_creates_config_with_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = os.path.join(tmpdir, "vendor", "tokensafe")
            os.makedirs(plugin_dir)
            with patch("os.getcwd", return_value=tmpdir):
                res = AgentInstaller.install_project(tmpdir, agents_plugin_path=plugin_dir)
            self.assertIn(".agents/plugins.json", res["created_rules"])
            cfg_path = os.path.join(tmpdir, ".agents", "plugins.json")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg["entries"], [{"path": "vendor/tokensafe"}])

    def test_merges_into_existing_config_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = os.path.join(tmpdir, ".agents")
            os.makedirs(agents_dir)
            cfg_path = os.path.join(agents_dir, "plugins.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"inherits": ["base.json"], "entries": [{"path": "other-plugin"}]}, f)

            outside = tempfile.mkdtemp()
            self.addCleanup(__import__("shutil").rmtree, outside, True)
            with patch("os.getcwd", return_value=tmpdir):
                AgentInstaller.install_project(tmpdir, agents_plugin_path=outside)
                AgentInstaller.install_project(tmpdir, agents_plugin_path=outside)

            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg["inherits"], ["base.json"])
            self.assertEqual(len(cfg["entries"]), 2)
            self.assertEqual(cfg["entries"][0], {"path": "other-plugin"})
            self.assertEqual(cfg["entries"][1], {"path": outside})

    def test_not_created_without_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            AgentInstaller.install_project(tmpdir)
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".agents", "plugins.json")))

    def test_self_update_clears_cache_and_reports_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "data")
            os.makedirs(data_dir)
            cache_file = os.path.join(data_dir, "version_cache.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write('{"client_version": "2.2.1"}')

            calls = []

            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch.dict(os.environ, {"TOKENSAFE_DATA_DIR": data_dir}), \
                 patch("src.installer.PROJECT_ROOT", tmpdir), \
                 patch.object(AgentInstaller, "_find_npm", return_value=None), \
                 patch("src.installer.shutil.which", side_effect=lambda name: "/usr/local/bin/tokensafe" if name == "tokensafe" else None), \
                 patch.object(AgentInstaller, "detect_available_agents", return_value={}), \
                 patch.object(AgentInstaller, "restart_mcp_daemons", return_value=0), \
                 patch("src.installer.subprocess.run", side_effect=fake_run):
                AgentInstaller.self_update()

            self.assertFalse(os.path.exists(cache_file))
            self.assertIn(["/usr/local/bin/tokensafe", "version", "--check"], calls)

if __name__ == "__main__":
    unittest.main()
