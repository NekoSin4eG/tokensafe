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
import subprocess
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
    "5. **Command Sanitization**: Use `sanitize_command` on high-output terminal commands (`git diff`, `pytest`, `find`, `grep`) to prevent lockfile or test log context contamination.\n"
    "6. **Proof-of-Savings Turn Footer (English default)**:\n"
    "At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:\n"
    "`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens ($<usd>)`\n"
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

    @classmethod
    def install_mcp_config(cls, agent_key: str) -> Tuple[bool, str]:
        """Injects TokenSafe MCP config into target agent."""
        info = AGENTS_REGISTRY.get(agent_key)
        if not info:
            return False, f"Unknown agent: {agent_key}"

        if info["type"] == "plugin":
            # Symlink plugin directory
            target = info["target_path"]
            cls._symlink_dir(PROJECT_ROOT, target)
            if "legacy_path" in info:
                cls._symlink_dir(PROJECT_ROOT, info["legacy_path"])
            return True, f"Symlinked plugin into {target}"

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
    def install_project(cls, project_dir: Optional[str] = None) -> Dict[str, Any]:
        """Injects TokenSafe rules and MCP configs directly into a target project workspace."""
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

        return {
            "success": True,
            "project_dir": project_dir,
            "created_rules": created
        }

    @classmethod
    def install_all(cls, force: bool = False) -> Dict[str, Any]:
        """Automatically installs TokenSafe to all detected (or all registered) AI agents."""
        agents = cls.detect_available_agents()
        results = {}
        for key, info in agents.items():
            if info["installed"] or force:
                ok, msg = cls.install_mcp_config(key)
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
                    print(f"  \033[32m✔\033[0m \033[1m{name}\033[0m: {msg}")
                    installed_count += 1
                else:
                    print(f"  \033[31m✖\033[0m \033[1m{name}\033[0m: {msg}")

        print(f"\n\033[32m\033[1m✅ TokenSafe successfully connected to {installed_count} agent(s)!\033[0m")
        print("   Restart your IDE / Agent to start saving tokens automatically.\n")

    @classmethod
    def self_update(cls):
        """
        One-click self update for TokenSafe:
        1. Detects package installation source (git, npm, pip).
        2. Upgrades TokenSafe engine to latest release.
        3. Re-synchronizes MCP configs and rules for all active agents.
        4. Refreshes local version cache.
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
        if shutil.which("npm"):
            try:
                res = subprocess.run(["npm", "list", "-g", "--depth=0", "tokensafe"], capture_output=True, text=True, timeout=5)
                if "tokensafe@" in res.stdout:
                    print(f"  {CYAN}→{RESET} Updating global npm package (npm install -g tokensafe@latest)...")
                    up_res = subprocess.run(["npm", "install", "-g", "tokensafe@latest"], capture_output=True, text=True, timeout=30)
                    if up_res.returncode == 0:
                        print(f"  {GREEN}✔{RESET} npm package upgraded successfully.")
            except Exception:
                pass

        # 3. Check pip
        pip_cmd = "pip3" if shutil.which("pip3") else ("pip" if shutil.which("pip") else None)
        if pip_cmd:
            try:
                res = subprocess.run([pip_cmd, "list"], capture_output=True, text=True, timeout=5)
                if "tokensafe" in res.stdout:
                    print(f"  {CYAN}→{RESET} Updating Python package ({pip_cmd} install --upgrade tokensafe)...")
                    up_res = subprocess.run([pip_cmd, "install", "--upgrade", "tokensafe"], capture_output=True, text=True, timeout=30)
                    if up_res.returncode == 0:
                        print(f"  {GREEN}✔{RESET} Python package upgraded successfully.")
            except Exception:
                pass

        # 4. Re-synchronize all detected agents
        print(f"\n  {CYAN}→{RESET} Re-synchronizing AI Agent MCP configs & rules...")
        agents = cls.detect_available_agents()
        synced_count = 0
        for key, info in agents.items():
            if info["installed"]:
                ok, msg = cls.install_mcp_config(key)
                if ok:
                    print(f"    {GREEN}✔{RESET} {info['name']}: {msg}")
                    synced_count += 1
                else:
                    print(f"    {YELLOW}⚠{RESET} {info['name']}: {msg}")

        # 5. Refresh local version cache
        try:
            data_dir = os.path.join(PROJECT_ROOT, "data")
            cache_file = os.path.join(data_dir, "version_cache.json")
            if os.path.exists(cache_file):
                os.remove(cache_file)
        except Exception:
            pass

        print(f"\n{BOLD}{GREEN}✅ Self-update complete!{RESET}")
        print(f"   Synchronized {synced_count} agent environments with zero-loss AST & God Nodes rules.")
        print(f"   Run {BOLD}tokensafe stats{RESET} to view token savings.\n")

def main():
    args = sys.argv[1:]
    if args and args[0] in ("update", "--update", "upgrade", "--upgrade"):
        AgentInstaller.self_update()
    else:
        AgentInstaller.run_interactive()

if __name__ == "__main__":
    main()
