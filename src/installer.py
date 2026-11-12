"""
Universal Interactive Agent Installer for TokenSafe.
Scans and automatically configures MCP integration for:
- Google Antigravity
- Claude Desktop
- Cursor
- Windsurf
- Cline / Roo Code (VS Code)
- OpenAI Codex
- Z.ai Code
"""

import os
import sys
import json
import shutil
import re
import shlex
import signal
import subprocess
import time
from typing import Dict, List, Any, Optional, Tuple

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"

HOME = os.path.expanduser("~")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_SERVER_PATH = os.path.join(PROJECT_ROOT, "src", "mcp_server.py")
PYTHON_BIN = sys.executable

UNIFIED_TOKENSAFE_RULE = (
    "## TOKENSAFE GRAPH-FIRST OPTIMIZATION PROTOCOL\n"
    "Always use `tokensafe` MCP tools over default raw file reads or high-output terminal executions to prevent context window exhaustion.\n\n"
    "1. **Zero Raw Grep/Find (Blocked)**: Strictly NEVER execute terminal `grep`, `egrep`, `ripgrep`, or `find` on source code files or directories (`src/`, `views/`, `components/`, `.ts`, `.tsx`, `.py`, `.js`). These commands are hard-blocked by TokenSafe Guard.\n"
    "2. **Mandatory Symbol & Component Inspection**: Whenever locating or reading a function, class, component, or method, ALWAYS call `tokensafe:smart_read_file` with `path` and `symbol`. It preserves the full executable target and 1-hop dependencies while folding inert code into signatures (-85% to -92% tokens).\n"
    "3. **Architecture Discovery**: Always call `tokensafe:get_architecture_graph(project_path)` before exploratory multi-file reading. Returns project God Nodes and key dependency hubs in < 250 tokens without file crawling.\n"
    "4. **API Surface Map**: Use `tokensafe:get_api_surface(project_path)` to get all exported classes, types, and interfaces in a single markdown map.\n"
    "5. **Command Sanitization**: Use `sanitize_command` on high-output terminal commands (`git diff/log/show/blame`, `rg`, `find`, `grep`, `docker`/`kubectl logs`, `npm`/`pip install`, `cat` of lockfiles, `curl`) — compound commands (`a && b ; c`) are rewritten per stage. Run unit tests via the `tokensafe tests [pytest]` CLI (compact failure report) instead of raw `python3 -m unittest discover`; never leave `vitest`/`jest` in watch mode — pass the optional `cwd` to `sanitize_command` for test commands so the package.json peek can identify the runner behind opaque `yarn/npm test` scripts.\n"
    "6. **Proof-of-Savings Turn Footer (always in English)**:\n"
    "At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:\n"
    "`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`\n"
)

AGENTS_REGISTRY = {
    "antigravity": {
        "name": "Google Antigravity",
        "type": "plugin",
        "target_path": os.path.join(HOME, ".gemini", "config", "plugins", "tokensafe"),
        "legacy_path": os.path.join(HOME, ".gemini", "config", "plugins", "agy-token-optimizer"),
        "detect_path": os.path.join(HOME, ".gemini")
    },
    "claude_desktop": {
        "name": "Claude Desktop",
        "type": "mcp_json",
        "config_path": os.path.join(HOME, "Library", "Application Support", "Claude", "claude_desktop_config.json"),
        "detect_path": os.path.join(HOME, "Library", "Application Support", "Claude")
    },
    "cursor": {
        "name": "Cursor",
        "type": "cursor_env",
        "config_path": os.path.join(HOME, ".cursor", "mcp.json"),
        "skills_path": os.path.join(HOME, ".cursor", "skills", "token-optimizer"),
        "rules_path": os.path.join(HOME, ".cursor", "rules", "tokensafe.mdc"),
        "extension_path": os.path.join(HOME, ".cursor", "extensions", "tokensafe-1.0.0"),
        "detect_path": os.path.join(HOME, ".cursor")
    },
    "windsurf": {
        "name": "Windsurf",
        "type": "mcp_json",
        "config_path": os.path.join(HOME, ".codeium", "windsurf", "mcp_config.json"),
        "rules_path": os.path.join(HOME, ".codeium", "windsurf", "memories", "global_rules.md"),
        "detect_path": os.path.join(HOME, ".codeium", "windsurf")
    },
    "cline": {
        "name": "Cline / Roo Code (VS Code)",
        "type": "mcp_json",
        "config_path": os.path.join(HOME, "Library", "Application Support", "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json"),
        "rules_path": os.path.join(HOME, "Library", "Application Support", "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings", "Rule_TokenSafe.md"),
        "detect_path": os.path.join(HOME, "Library", "Application Support", "Code")
    },
    "claude_code": {
        "name": "Claude Code CLI",
        "type": "claude_md",
        "config_path": os.path.join(HOME, ".claude.json"),
        "agents_md_path": os.path.join(HOME, ".claude", "CLAUDE.md"),
        "skills_path": os.path.join(HOME, ".claude", "skills", "token-optimizer"),
        "detect_path": os.path.join(HOME, ".claude")
    },
    "codex": {
        "name": "OpenAI Codex",
        "type": "codex_toml",
        "config_path": os.path.join(HOME, ".codex", "config.toml"),
        "skills_path": os.path.join(HOME, ".codex", "skills", "token-optimizer"),
        "agents_md_path": os.path.join(HOME, ".codex", "AGENTS.md"),
        "plugin_path": os.path.join(HOME, ".codex", "plugins", "tokensafe"),
        "detect_path": os.path.join(HOME, ".codex")
    },
    "copilot": {
        "name": "GitHub Copilot",
        "type": "copilot_json",
        "config_path": os.path.join(HOME, ".copilot", "mcp-config.json"),
        "detect_path": os.path.join(HOME, ".copilot")
    },
    "zcode": {
        "name": "Z.ai Code",
        "type": "zcode_json",
        "config_path": os.path.join(HOME, ".zcode", "cli", "config.json"),
        "skills_path": os.path.join(HOME, ".zcode", "skills", "token-optimizer"),
        "plugin_path": os.path.join(HOME, ".zcode", "cli", "plugins", "tokensafe"),
        "agents_md_path": os.path.join(HOME, ".zcode", "AGENTS.md"),
        "detect_path": os.path.join(HOME, ".zcode")
    }
}

class AgentInstaller:
    """Manages multi-agent detection and configuration injection."""

    @classmethod
    def detect_available_agents(cls) -> Dict[str, Dict[str, Any]]:
        """Scans filesystem to find which AI agent platforms are installed."""
        detected = {}
        for key, info in AGENTS_REGISTRY.items():
            is_present = os.path.exists(info["detect_path"])
            # Fallback for cursor / windsurf on macOS
            if not is_present and key == "cursor":
                is_present = os.path.exists(os.path.join(HOME, "Library", "Application Support", "Cursor"))
            detected[key] = {
                **info,
                "installed": is_present
            }
        return detected

    @staticmethod
    def _tokensafe_hook_command(script_name: str) -> str:
        """Shell command string for a bundled hook adapter (space-safe)."""
        script = os.path.join(PROJECT_ROOT, "hooks", script_name)
        return f"{shlex.quote(PYTHON_BIN)} {shlex.quote(script)}"

    @classmethod
    def install_hooks_config(cls, agent_key: str) -> Tuple[bool, str]:
        """Registers TokenSafe lifecycle-hook adapters with agents that support them.
        Claude Code: SessionStart (one-shot directives) + UserPromptSubmit (TOCP
        injection) + PreToolUse (shell guard).
        Cursor: beforeShellExecution / beforeReadFile guards.
        Codex has no lifecycle hooks (MCP + AGENTS.md remain the integration)."""
        if agent_key == "codex":
            return False, "Codex CLI has no lifecycle hooks — MCP + AGENTS.md stay the integration"
        if agent_key not in ("claude_code", "cursor"):
            return False, "hooks not applicable for this agent"

        claude_cmd = cls._tokensafe_hook_command("claude_code_hook.py")
        cursor_cmd = cls._tokensafe_hook_command("cursor_hook.py")

        if agent_key == "claude_code":
            info = AGENTS_REGISTRY["claude_code"]
            config_file = info.get("agents_md_path", "").replace("CLAUDE.md", "settings.json")
            config_file = os.path.join(HOME, ".claude", "settings.json")
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            data: Dict[str, Any] = {}
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
            blob = json.dumps(hooks)
            if "claude_code_hook.py" in blob:
                return True, "TokenSafe Claude Code hooks already registered"
            prompt_entry = {"hooks": [{"type": "command", "command": claude_cmd, "timeout": 10}]}
            bash_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": claude_cmd, "timeout": 10}]}
            # SessionStart: no matcher = all sources (startup/resume/clear/compact).
            session_entry = {"hooks": [{"type": "command", "command": claude_cmd, "timeout": 10}]}
            hooks.setdefault("SessionStart", []).append(session_entry)
            hooks.setdefault("UserPromptSubmit", []).append(prompt_entry)
            hooks.setdefault("PreToolUse", []).append(bash_entry)
            data["hooks"] = hooks
            cls._write_file_safely(config_file, json.dumps(data, indent=2))
            return True, "hooks registered (SessionStart + UserPromptSubmit + PreToolUse guard)"

        # cursor
        config_file = os.path.join(HOME, ".cursor", "hooks.json")
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        data = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        blob = json.dumps(hooks)
        if "cursor_hook.py" in blob:
            return True, "TokenSafe Cursor hooks already registered"
        shell_entry = {"command": cursor_cmd, "timeout": 10}
        hooks.setdefault("beforeShellExecution", []).append(dict(shell_entry))
        hooks.setdefault("beforeReadFile", []).append(dict(shell_entry))
        hooks.setdefault("beforeSubmitPrompt", []).append(dict(shell_entry))
        data["hooks"] = hooks
        cls._write_file_safely(config_file, json.dumps(data, indent=2))
        return True, "guards registered (shell + junk-read + prompt pipeline)"

    @classmethod
    def install_mcp_config(cls, agent_key: str) -> Tuple[bool, str]:
        """Injects TokenSafe MCP config into target agent."""
        info = AGENTS_REGISTRY.get(agent_key)
        if not info:
            return False, f"Unknown agent: {agent_key}"

        if info["type"] == "plugin":
            # Symlink the plugin under its canonical name only. Antigravity keys
            # plugin enable-state by directory name (plugins.md), so a second
            # legacy-named symlink registers the plugin twice and runs every
            # hook twice per turn. Remove only stale legacy SYMLINKS — a real
            # directory there is user data and must not be touched.
            target = info["target_path"]
            cls._symlink_dir(PROJECT_ROOT, target)
            legacy = info.get("legacy_path")
            if legacy:
                try:
                    if os.path.islink(legacy):
                        os.unlink(legacy)
                except Exception:
                    pass
            # Once the plugin declares its own mcp_config.json (loaded by
            # Antigravity together with the plugin), a leftover user-level
            # tokensafe entry would register the MCP server twice. Migrate it
            # away — Unix only, the plugin-root sh shim is not cmd-compatible.
            migrated = False
            if os.name != "nt" and os.path.isfile(os.path.join(PROJECT_ROOT, "mcp_config.json")):
                migrated = cls._remove_user_level_tokensafe_mcp()
            msg = f"Symlinked plugin into {target}"
            if migrated:
                msg += "; migrated user-level MCP entry to the plugin-declared server"
            return True, msg

        elif info["type"] == "cursor_env":
            config_file = info["config_path"]
            os.makedirs(os.path.dirname(config_file), exist_ok=True)

            data = {}
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    shutil.copy2(config_file, f"{config_file}.backup")
                except Exception:
                    data = {}

            if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                data["mcpServers"] = {}

            data["mcpServers"]["tokensafe"] = {
                "command": PYTHON_BIN,
                "args": [MCP_SERVER_PATH]
            }

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            if "skills_path" in info:
                cls._symlink_skill(info["skills_path"])

            if "extension_path" in info:
                ext_src = os.path.join(PROJECT_ROOT, "extensions", "cursor-tokensafe")
                if os.path.exists(ext_src):
                    cls._symlink_dir(ext_src, info["extension_path"])

            if "rules_path" in info:
                rule_text = (
                    "---\n"
                    "description: TokenSafe token economy, graph-first navigation and context optimization protocol\n"
                    "globs: *\n"
                    "alwaysApply: true\n"
                    "---\n\n"
                    + UNIFIED_TOKENSAFE_RULE
                )
                cls._write_file_safely(info["rules_path"], rule_text)
                cursorrules = os.path.join(HOME, ".cursorrules")
                cls._inject_global_rule(cursorrules, UNIFIED_TOKENSAFE_RULE)

            return True, f"Configured MCP, Extension & Rule for Cursor"

        elif info["type"] == "mcp_json":
            config_file = info["config_path"]
            os.makedirs(os.path.dirname(config_file), exist_ok=True)

            data = {}
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            if "mcpServers" not in data:
                data["mcpServers"] = {}

            data["mcpServers"]["tokensafe"] = {
                "command": PYTHON_BIN,
                "args": [MCP_SERVER_PATH]
            }

            # Backup existing
            if os.path.exists(config_file):
                try:
                    shutil.copy2(config_file, f"{config_file}.backup")
                except Exception:
                    pass

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            if "skills_path" in info:
                cls._symlink_skill(info["skills_path"])

            if "rules_path" in info:
                cls._inject_global_rule(info["rules_path"], UNIFIED_TOKENSAFE_RULE)

            return True, f"Updated MCP configuration at {config_file}"

        elif info["type"] == "codex_toml":
            config_file = info["config_path"]
            os.makedirs(os.path.dirname(config_file), exist_ok=True)

            content = ""
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    shutil.copy2(config_file, f"{config_file}.backup")
                except Exception:
                    pass

            server_snippet = (
                f'\n[mcp_servers.tokensafe]\n'
                f'command = "{PYTHON_BIN}"\n'
                f'args = ["{MCP_SERVER_PATH}"]\n'
            )

            if '[mcp_servers.tokensafe]' in content:
                pattern = r'\[mcp_servers\.tokensafe\][^\[]*'
                content = re.sub(pattern, f'[mcp_servers.tokensafe]\ncommand = "{PYTHON_BIN}"\nargs = ["{MCP_SERVER_PATH}"]\n\n', content)
            else:
                content = content.rstrip() + "\n" + server_snippet

            with open(config_file, "w", encoding="utf-8") as f:
                f.write(content)

            if "skills_path" in info:
                cls._symlink_skill(info["skills_path"])

            if "plugin_path" in info:
                cls._symlink_dir(PROJECT_ROOT, info["plugin_path"])

            if "agents_md_path" in info:
                cls._inject_global_rule(info["agents_md_path"], UNIFIED_TOKENSAFE_RULE)

            return True, f"Configured MCP, Plugin, Skill & AGENTS.md in {config_file}"

        elif info["type"] == "claude_md":
            if "config_path" in info:
                config_file = info["config_path"]
                data = {}
                if os.path.exists(config_file):
                    try:
                        with open(config_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                    data["mcpServers"] = {}
                data["mcpServers"]["tokensafe"] = {
                    "command": PYTHON_BIN,
                    "args": [MCP_SERVER_PATH]
                }
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

            if "agents_md_path" in info:
                cls._inject_global_rule(info["agents_md_path"], UNIFIED_TOKENSAFE_RULE)

            if "skills_path" in info:
                cls._symlink_skill(info["skills_path"])

            return True, f"Configured MCP, Rule & Skill in {info.get('agents_md_path')}"

        elif info["type"] == "copilot_json":
            config_file = info["config_path"]
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            data = {}
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                data["mcpServers"] = {}
            data["mcpServers"]["tokensafe"] = {
                "type": "stdio",
                "command": PYTHON_BIN,
                "args": [MCP_SERVER_PATH],
                "tools": ["*"]
            }
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True, f"Configured MCP for GitHub Copilot in {config_file}"

        elif info["type"] == "zcode_json":
            config_file = info["config_path"]
            os.makedirs(os.path.dirname(config_file), exist_ok=True)

            data = {}
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    shutil.copy2(config_file, f"{config_file}.backup")
                except Exception:
                    data = {}

            if "mcp" not in data or not isinstance(data["mcp"], dict):
                data["mcp"] = {}
            if "servers" not in data["mcp"] or not isinstance(data["mcp"]["servers"], dict):
                data["mcp"]["servers"] = {}

            data["mcp"]["servers"]["tokensafe"] = {
                "command": PYTHON_BIN,
                "args": [MCP_SERVER_PATH]
            }

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            if "skills_path" in info:
                cls._symlink_skill(info["skills_path"])

            if "plugin_path" in info:
                cls._symlink_dir(PROJECT_ROOT, info["plugin_path"])

            if "agents_md_path" in info:
                cls._inject_global_rule(info["agents_md_path"], UNIFIED_TOKENSAFE_RULE)

            return True, f"Configured MCP, Plugin, Skill & AGENTS.md in {config_file}"

        return False, "Unsupported type"

    @staticmethod
    def _remove_user_level_tokensafe_mcp() -> bool:
        """Removes the tokensafe MCP entry from Antigravity's user-level
        mcp_config.json files (backup first). Returns True if any entry was
        removed. Entries under disabledMcpServers are left alone: the user
        explicitly disabled the server there."""
        removed = False
        for config_path in (
            os.path.join(HOME, ".gemini", "config", "mcp_config.json"),
            os.path.join(HOME, ".gemini", "antigravity", "mcp_config.json"),
        ):
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                servers = data.get("mcpServers")
                if isinstance(servers, dict) and "tokensafe" in servers:
                    shutil.copy2(config_path, f"{config_path}.backup")
                    servers.pop("tokensafe")
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    removed = True
            except Exception:
                continue
        return removed

    @classmethod
    def _symlink_dir(cls, source_path: str, target_path: str):
        """Safely creates or replaces a symlink to a directory."""
        if not os.path.exists(source_path):
            return
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.islink(target_path) or os.path.exists(target_path):
            try:
                if os.path.islink(target_path) or os.path.isfile(target_path):
                    os.unlink(target_path)
                else:
                    shutil.rmtree(target_path)
            except Exception:
                pass
        try:
            os.symlink(source_path, target_path)
        except Exception:
            pass

    @classmethod
    def _write_file_safely(cls, file_path: str, content: str):
        """Safely writes content creating parent directories."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    @classmethod
    def _inject_global_rule(cls, file_path: str, rule_snippet: str, tag: str = "tokensafe"):
        """Injects or updates a delimited rule block in a markdown instruction file."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        content = ""
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = ""
        start_tag = f"<!-- {tag} -->"
        end_tag = f"<!-- /{tag} -->"
        pattern = re.compile(rf"{re.escape(start_tag)}.*?{re.escape(end_tag)}", re.DOTALL)
        wrapped = f"{start_tag}\n{rule_snippet.strip()}\n{end_tag}"
        if start_tag in content:
            new_content = pattern.sub(wrapped, content)
        else:
            new_content = (content.rstrip() + "\n\n" + wrapped).lstrip()
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    @classmethod
    def _symlink_skill(cls, target_path: str):
        """Symlinks the TokenSafe skill into the target agent's skills directory."""
        skill_src = os.path.join(PROJECT_ROOT, "skills", "token-optimizer")
        cls._symlink_dir(skill_src, target_path)

    @classmethod
    def install_project(
        cls,
        project_dir: Optional[str] = None,
        agents_plugin_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Injects TokenSafe rules and MCP configs directly into a target project workspace.

        agents_plugin_path (opt-in): declares the tokensafe plugin directory in
        the project's .agents/plugins.json (json_configs.md) so teammates on
        Antigravity load the plugin from the shared repo without running the
        installer themselves."""
        if not project_dir:
            project_dir = os.getcwd()
        project_dir = os.path.abspath(project_dir)

        rule_files = [
            os.path.join(project_dir, "AGENTS.md"),
            os.path.join(project_dir, "CLAUDE.md"),
            os.path.join(project_dir, ".cursorrules"),
            os.path.join(project_dir, ".windsurfrules"),
            os.path.join(project_dir, ".clinerules"),
            os.path.join(project_dir, ".github", "copilot-instructions.md"),
            os.path.join(project_dir, ".cursor", "rules", "tokensafe.mdc"),
        ]

        cursor_mdc_header = (
            "---\n"
            "description: TokenSafe token economy, graph-first navigation and context optimization protocol\n"
            "globs: *\n"
            "alwaysApply: true\n"
            "---\n\n"
        )

        created = []
        for rf in rule_files:
            try:
                if rf.endswith(".mdc"):
                    cls._write_file_safely(rf, cursor_mdc_header + UNIFIED_TOKENSAFE_RULE)
                else:
                    cls._inject_global_rule(rf, UNIFIED_TOKENSAFE_RULE)
                created.append(os.path.relpath(rf, project_dir))
            except Exception:
                pass

        # Also inject project-local .cursor/mcp.json
        cursor_mcp = os.path.join(project_dir, ".cursor", "mcp.json")
        try:
            os.makedirs(os.path.dirname(cursor_mcp), exist_ok=True)
            data = {}
            if os.path.exists(cursor_mcp):
                try:
                    with open(cursor_mcp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                data["mcpServers"] = {}
            data["mcpServers"]["tokensafe"] = {
                "command": PYTHON_BIN,
                "args": [MCP_SERVER_PATH]
            }
            with open(cursor_mcp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            created.append(".cursor/mcp.json")
        except Exception:
            pass

        if agents_plugin_path:
            cls._write_agents_plugins_config(project_dir, agents_plugin_path)
            created.append(".agents/plugins.json")

        return {
            "success": True,
            "project_dir": project_dir,
            "created_rules": created
        }

    @classmethod
    def _write_agents_plugins_config(cls, project_dir: str, plugin_path: str) -> None:
        """Writes/merges .agents/plugins.json declaring the tokensafe plugin
        directory. Stored relative to the project root when the plugin lives
        inside it (team-shareable), otherwise as an absolute path."""
        config_path = os.path.join(project_dir, ".agents", "plugins.json")
        config: Dict[str, Any] = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}

        abs_path = os.path.abspath(plugin_path if os.path.isabs(plugin_path) else os.path.join(os.getcwd(), plugin_path))
        if abs_path == project_dir or abs_path.startswith(project_dir + os.sep):
            stored = os.path.relpath(abs_path, project_dir).replace(os.sep, "/")
        else:
            stored = abs_path

        entries = config.get("entries")
        if not isinstance(entries, list):
            entries = []
        if not any(isinstance(e, dict) and e.get("path") == stored for e in entries):
            entries.append({"path": stored})
        config["entries"] = entries
        cls._write_file_safely(config_path, json.dumps(config, indent=2))

    @classmethod
    def install_all(cls, force: bool = False) -> Dict[str, Any]:
        """Automatically installs TokenSafe to all detected (or all registered) AI agents."""
        agents = cls.detect_available_agents()
        results = {}
        for key, info in agents.items():
            if info["installed"] or force:
                ok, msg = cls.install_mcp_config(key)
                if ok:
                    h_ok, h_msg = cls.install_hooks_config(key)
                    if h_ok:
                        msg = f"{msg}; {h_msg}"
                results[key] = {"name": info["name"], "success": ok, "message": msg}
        # Also configure project rules
        proj_res = cls.install_project(os.getcwd())
        return {
            "agents": results,
            "project_rules": proj_res.get("created_rules", [])
        }

    @classmethod
    def run_interactive(cls):
        """Interactive terminal UI asking developer which agents to connect."""
        agents = cls.detect_available_agents()

        print("\n\033[1m\033[36m╔════════════════════════════════════════════════════════════════════╗\033[0m")
        print("\033[1m\033[36m║         ⚡ TOKENSAFE UNIVERSAL MCP & AGENT INSTALLER ⚡          ║\033[0m")
        print("\033[1m\033[36m╚════════════════════════════════════════════════════════════════════╝\033[0m\n")
        print("Detected AI developer environments on your machine:\n")

        keys = list(agents.keys())
        selected = {}

        for idx, key in enumerate(keys, 1):
            info = agents[key]
            tag = "\033[32m[Detected]\033[0m" if info["installed"] else "\033[2m[Not Found]\033[0m"
            # Auto-select detected platforms
            selected[key] = info["installed"]
            mark = "[\033[32m✓\033[0m]" if selected[key] else "[ ]"
            print(f"  {idx}. {mark} {info['name']:<28} {tag}")

        print("\n\033[2mEnter numbers to toggle (e.g., 2,3), 'A' for all, or press Enter to install:\033[0m")
        choice = input("> ").strip().lower()

        if choice == "a":
            for k in selected:
                selected[k] = True
        elif choice:
            for num in choice.replace(",", " ").split():
                if num.isdigit() and 1 <= int(num) <= len(keys):
                    k = keys[int(num) - 1]
                    selected[k] = not selected[k]

        print("\nInstalling TokenSafe configurations...\n")
        installed_count = 0
        for key, is_sel in selected.items():
            if is_sel:
                name = agents[key]["name"]
                ok, msg = cls.install_mcp_config(key)
                if ok:
                    h_ok, h_msg = cls.install_hooks_config(key)
                    if h_ok:
                        msg = f"{msg}; {h_msg}"
                if ok:
                    print(f"  \033[32m✔\033[0m \033[1m{name}\033[0m: {msg}")
                    installed_count += 1
                else:
                    print(f"  \033[31m✖\033[0m \033[1m{name}\033[0m: {msg}")

        print(f"\n\033[32m\033[1m✅ TokenSafe successfully connected to {installed_count} agent(s)!\033[0m")
        print("   Restart your IDE / Agent to start saving tokens automatically.\n")

    @staticmethod
    def _is_tokensafe_mcp_command(cmd: str) -> bool:
        """Returns True if a process command line represents a running TokenSafe MCP server daemon."""
        if not cmd or not cmd.strip():
            return False
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            return False

        # Ignore shell wrappers or inline python -c inspection commands
        exe_base = os.path.basename(tokens[0]).lower()
        if exe_base in ("sh", "bash", "zsh", "fish", "dash") and "-c" in tokens:
            return False
        if exe_base.startswith("python") and "-c" in tokens:
            return False

        for idx, tok in enumerate(tokens):
            base = os.path.basename(tok).lower()
            if base == "mcp_server.py" and ("tokensafe" in tok.lower() or "agy-token-optimizer" in tok.lower() or "src/mcp_server.py" in tok.replace("\\", "/")):
                return True
            if base in ("tokensafe", "tokensafe.js", "agy-tokens"):
                rest = tokens[idx + 1:]
                if rest and rest[0] == "mcp":
                    subcmd = rest[1] if len(rest) > 1 else "serve"
                    if subcmd not in ("disable", "off", "rm", "enable", "on", "add", "list", "ls", "status"):
                        return True
        return False

    @classmethod
    def restart_mcp_daemons(cls) -> int:
        """
        Touches the MCP reload marker and terminates active TokenSafe MCP server daemons
        so auto-reconnecting IDEs (Antigravity, Cursor, Claude Code) spawn the updated version.
        """
        try:
            # Data-dir resolution is context-independent (canonical
            # ~/.tokensafe/data unless TOKENSAFE_DATA_DIR overrides), so one
            # marker location covers every launch context.
            from src.paths import resolve_data_dir
            data_dir = resolve_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            reload_marker = os.path.join(data_dir, ".mcp_reload")
            with open(reload_marker, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass

        if os.name == "nt":
            return 0

        my_pid = os.getpid()
        parent_pid = os.getppid()
        target_pids: List[int] = []

        try:
            res = subprocess.run(
                ["ps", "-ax", "-o", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) < 2:
                        continue
                    try:
                        pid = int(parts[0])
                    except ValueError:
                        continue
                    if pid in (my_pid, parent_pid) or pid <= 1:
                        continue
                    if cls._is_tokensafe_mcp_command(parts[1]):
                        target_pids.append(pid)
        except Exception:
            return 0

        restarted = 0
        for pid in target_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                restarted += 1
            except (ProcessLookupError, PermissionError, OSError):
                continue

        if restarted > 0:
            time.sleep(0.2)
            for pid in target_pids:
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        return restarted

    @staticmethod
    def _find_npm(home: Optional[str] = None) -> Optional[str]:
        """Locates an npm executable even in a clean non-interactive environment.

        The updater dialog runs via `do shell script` + login shell, where .zshrc
        (and therefore nvm) is never sourced; PATH-only discovery made the npm
        channel silently skip updates. Falls back to well-known locations."""
        found = shutil.which("npm")
        if found:
            return found
        base = home or HOME
        candidates: List[str] = []
        nvm_dir = os.path.join(base, ".nvm", "versions", "node")
        try:
            if os.path.isdir(nvm_dir):
                # Newest installed node version wins.
                for version_dir in sorted(os.listdir(nvm_dir), reverse=True):
                    candidates.append(os.path.join(nvm_dir, version_dir, "bin", "npm"))
        except OSError:
            pass
        candidates += [
            "/opt/homebrew/bin/npm",
            "/usr/local/bin/npm",
            "/usr/bin/npm",
        ]
        for cand in candidates:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return None

    @classmethod
    def self_update(cls):
        """
        One-click self update for TokenSafe:
        1. Detects package installation source (git, npm, pip).
        2. Upgrades TokenSafe engine to latest release.
        3. Re-synchronizes MCP configs and rules for all active agents.
        4. Refreshes local version cache and restarts running MCP daemons.
        """
        print(f"\n{BOLD}{CYAN}╔════════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{BOLD}{CYAN}║             ⚡ TOKENSAFE ONE-CLICK SELF-UPDATE ⚡                 ║{RESET}")
        print(f"{BOLD}{CYAN}╚════════════════════════════════════════════════════════════════════╝{RESET}\n")

        # 1. Check if git clone installation (e.g. ~/.tokensafe)
        is_git_clone = os.path.isdir(os.path.join(PROJECT_ROOT, ".git"))
        if is_git_clone and PROJECT_ROOT.endswith((".tokensafe", "agy-token-optimizer")):
            print(f"  {CYAN}→{RESET} Detected local repository at {PROJECT_ROOT}. Pulling latest changes...")
            try:
                res = subprocess.run(["git", "pull", "--quiet"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    print(f"  {GREEN}✔{RESET} Git repository updated to latest commit.")
            except Exception as e:
                print(f"  {YELLOW}⚠{RESET} Git pull skipped: {e}")

        # 2. Check npm global
        # The updater dialog runs via `do shell script` + login shell, which skips
        # .zshrc — nvm is NOT on PATH there, so PATH-only discovery silently
        # downgraded the npm channel (the active one for many installs).
        npm_bin = cls._find_npm()
        if npm_bin:
            try:
                res = subprocess.run([npm_bin, "list", "-g", "--depth=0", "tokensafe"], capture_output=True, text=True, timeout=15)
                if "tokensafe@" in res.stdout:
                    print(f"  {CYAN}→{RESET} Updating global npm package (npm install -g tokensafe@latest)...")
                    up_res = subprocess.run([npm_bin, "install", "-g", "tokensafe@latest"], capture_output=True, text=True, timeout=120)
                    if up_res.returncode == 0:
                        print(f"  {GREEN}✔{RESET} npm package upgraded successfully.")
                    else:
                        detail = (up_res.stderr or up_res.stdout or "").strip()[:200]
                        print(f"  {YELLOW}⚠{RESET} npm upgrade failed: {detail}")
                else:
                    print(f"  {CYAN}ℹ{RESET} tokensafe not installed via npm — skipping npm channel.")
            except Exception as e:
                print(f"  {YELLOW}⚠{RESET} npm update skipped: {e}")
        else:
            print(f"  {YELLOW}⚠{RESET} npm not found — skipping npm channel.")

        # 3. Check pip
        pip_cmd = "pip3" if shutil.which("pip3") else ("pip" if shutil.which("pip") else None)
        if pip_cmd:
            try:
                res = subprocess.run([pip_cmd, "list"], capture_output=True, text=True, timeout=15)
                if "tokensafe" in res.stdout:
                    print(f"  {CYAN}→{RESET} Updating Python package ({pip_cmd} install --upgrade tokensafe)...")
                    up_res = subprocess.run([pip_cmd, "install", "--upgrade", "tokensafe"], capture_output=True, text=True, timeout=120)
                    if up_res.returncode == 0:
                        print(f"  {GREEN}✔{RESET} Python package upgraded successfully.")
                    else:
                        detail = (up_res.stderr or up_res.stdout or "").strip()[:200]
                        print(f"  {YELLOW}⚠{RESET} pip upgrade failed: {detail}")
            except Exception as e:
                print(f"  {YELLOW}⚠{RESET} pip update skipped: {e}")

        # 4. Re-synchronize all detected agents
        print(f"\n  {CYAN}→{RESET} Re-synchronizing AI Agent MCP configs & rules...")
        agents = cls.detect_available_agents()
        synced_count = 0
        for key, info in agents.items():
            if info["installed"]:
                ok, msg = cls.install_mcp_config(key)
                if ok:
                    h_ok, h_msg = cls.install_hooks_config(key)
                    if h_ok:
                        msg = f"{msg}; {h_msg}"
                if ok:
                    print(f"    {GREEN}✔{RESET} {info['name']}: {msg}")
                    synced_count += 1
                else:
                    print(f"    {YELLOW}⚠{RESET} {info['name']}: {msg}")

        # 5. Refresh local version cache and report the newly installed version in a fresh process
        try:
            from src.paths import resolve_data_dir
            cache_file = os.path.join(resolve_data_dir(), "version_cache.json")
            if os.path.exists(cache_file):
                os.remove(cache_file)
        except Exception:
            pass
        try:
            tokensafe_bin = shutil.which("tokensafe")
            sync_cmd = [tokensafe_bin, "version", "--check"] if tokensafe_bin else [sys.executable, os.path.join(PROJECT_ROOT, "src", "cli.py"), "version", "--check"]
            subprocess.run(sync_cmd, capture_output=True, text=True, timeout=5)
        except Exception:
            pass

        # 6. Restart running MCP server daemons so IDEs load the new version
        restarted_count = cls.restart_mcp_daemons()
        if restarted_count > 0:
            print(f"  {GREEN}✔{RESET} Restarted {restarted_count} running TokenSafe MCP daemon(s).")
            print(f"    {CYAN}ℹ{RESET} Antigravity/Cursor/Claude Code reconnect automatically; reload MCP in Claude Desktop/VS Code if disconnected.")

        print(f"\n{BOLD}{GREEN}✅ Self-update complete!{RESET}")
        print(f"   Synchronized {synced_count} agent environments with zero-loss AST & God Nodes rules.")
        print(f"   Run {BOLD}tokensafe stats{RESET} to view token savings.")
        print(f"   {CYAN}ℹ{RESET} If your terminal still shows the old version, open a new window or run {BOLD}hash -r{RESET}.")

def main():
    args = sys.argv[1:]
    if args and args[0] in ("update", "--update", "upgrade", "--upgrade"):
        AgentInstaller.self_update()
    else:
        AgentInstaller.run_interactive()

if __name__ == "__main__":
    main()
