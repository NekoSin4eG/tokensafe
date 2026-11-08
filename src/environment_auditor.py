"""
Environment Auditor & Context Bloat Guard for Antigravity Token Optimizer.
Detects heavy MCP servers, redundant tool schemas, and excessive skills that bloat
the system prompt on every API turn. Provides one-click enable/disable toggles.
"""

import os
import json
from typing import Dict, List, Any, Optional

EST_TOKENS_PER_TOOL = 25
EST_TOKENS_PER_SKILL = 180
CRITICAL_TOOL_COUNT_THRESHOLD = 40


class EnvironmentAuditor:
    """Scans and audits static context overhead (MCP servers and skills)."""

    @classmethod
    def get_config_paths(cls) -> List[str]:
        home = os.path.expanduser("~")
        return [
            os.path.join(home, ".gemini", "config", "mcp_config.json"),
            os.path.join(home, ".gemini", "antigravity", "mcp_config.json")
        ]

    @classmethod
    def get_mcp_schemas_dir(cls) -> str:
        return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "mcp")

    @classmethod
    def get_skills_dirs(cls) -> List[str]:
        home = os.path.expanduser("~")
        dirs = [
            os.path.join(home, ".gemini", "config", "skills"),
            os.path.join(home, ".gemini", "config", "plugins"),
            os.path.join(home, ".gemini", "antigravity", "builtin", "skills"),
        ]
        return [d for d in dirs if os.path.isdir(d)]

    @classmethod
    def get_configured_mcp_servers(cls) -> Dict[str, Any]:
        for p in cls.get_config_paths():
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return data.get("mcpServers", {})
                except Exception:
                    continue
        return {}

    @classmethod
    def audit(cls) -> Dict[str, Any]:
        """Audits all active MCP servers and skills for context footprint."""
        configured = cls.get_configured_mcp_servers()
        schemas_dir = cls.get_mcp_schemas_dir()

        servers = {}
        # 1. Discover all servers from schema directories
        if os.path.isdir(schemas_dir):
            for entry in os.listdir(schemas_dir):
                entry_path = os.path.join(schemas_dir, entry)
                if not os.path.isdir(entry_path):
                    continue

                is_disabled = entry.endswith(".disabled")
                srv_name = entry[:-9] if is_disabled else entry

                # Count tool schemas
                tool_files = [f for f in os.listdir(entry_path) if f.endswith(".json")]
                tool_count = len(tool_files)

                is_active = (not is_disabled) and (srv_name in configured)
                est_tokens = tool_count * EST_TOKENS_PER_TOOL

                servers[srv_name] = {
                    "name": srv_name,
                    "active": is_active,
                    "disabled": is_disabled,
                    "tool_count": tool_count,
                    "est_tokens_per_turn": est_tokens,
                    "is_bloat": is_active and (tool_count >= CRITICAL_TOOL_COUNT_THRESHOLD)
                }

        # Include configured servers that might not have a schema dir cached yet
        for srv_name in configured:
            if srv_name not in servers:
                servers[srv_name] = {
                    "name": srv_name,
                    "active": True,
                    "disabled": False,
                    "tool_count": 5,  # default estimate
                    "est_tokens_per_turn": 5 * EST_TOKENS_PER_TOOL,
                    "is_bloat": False
                }

        # 2. Count skills
        skills_count = 0
        for sdir in cls.get_skills_dirs():
            for root, dirs, files in os.walk(sdir):
                if "SKILL.md" in files:
                    skills_count += 1

        total_mcp_tools = sum(s["tool_count"] for s in servers.values() if s["active"])
        total_mcp_tokens = sum(s["est_tokens_per_turn"] for s in servers.values() if s["active"])
        total_skills_tokens = skills_count * EST_TOKENS_PER_SKILL
        total_static_tokens = total_mcp_tokens + total_skills_tokens

        bloated_servers = [s for s in servers.values() if s["is_bloat"]]

        return {
            "servers": servers,
            "total_active_servers": len([s for s in servers.values() if s["active"]]),
            "total_tools": total_mcp_tools,
            "total_skills": skills_count,
            "mcp_tokens_per_turn": total_mcp_tokens,
            "skills_tokens_per_turn": total_skills_tokens,
            "total_static_tokens_per_turn": total_static_tokens,
            "bloated_servers": bloated_servers,
            "has_critical_bloat": len(bloated_servers) > 0
        }

    @classmethod
    def get_bloat_warning(cls) -> Optional[str]:
        """Returns a formatted warning string if critical context bloat is detected."""
        report = cls.audit()
        if not report["has_critical_bloat"]:
            return None

        bloated = report["bloated_servers"]
        names = ", ".join([f"`{b['name']}` ({b['tool_count']} инструментов, ~{b['est_tokens_per_turn']:,} токенов/шаг)" for b in bloated])
        return (
            f"⚠️ **TokenSafe Bloat Alert**: Обнаружен тяжелый MCP сервер: {names}. "
            f"Суммарный налог на каждый запрос: **~{report['total_static_tokens_per_turn']:,} токенов**. "
            f"Отключите неиспользуемые серверы через `./bin/agy-tokens mcp disable <name>`."
        )

    @classmethod
    def disable_mcp(cls, server_name: str) -> Dict[str, Any]:
        """Disables an MCP server in mcp_config.json and renames its schema directory."""
        server_name = server_name.strip()
        schemas_dir = cls.get_mcp_schemas_dir()

        # 1. Update config files
        modified = False
        server_config = None
        for p in cls.get_config_paths():
            real_path = os.path.realpath(p)
            if os.path.isfile(real_path):
                try:
                    with open(real_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    mcp_servers = data.get("mcpServers", {})
                    if server_name in mcp_servers:
                        server_config = mcp_servers.pop(server_name)
                        # Save disabled config
                        if "disabledMcpServers" not in data:
                            data["disabledMcpServers"] = {}
                        data["disabledMcpServers"][server_name] = server_config
                        with open(real_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2)
                        modified = True
                except Exception:
                    pass

        # 2. Rename schema directory to .disabled
        active_dir = os.path.join(schemas_dir, server_name)
        disabled_dir = os.path.join(schemas_dir, f"{server_name}.disabled")
        renamed = False
        if os.path.isdir(active_dir):
            try:
                os.rename(active_dir, disabled_dir)
                renamed = True
            except Exception:
                pass

        return {
            "success": modified or renamed,
            "server_name": server_name,
            "config_updated": modified,
            "directory_renamed": renamed
        }

    @classmethod
    def enable_mcp(cls, server_name: str) -> Dict[str, Any]:
        """Re-enables a previously disabled MCP server."""
        server_name = server_name.strip()
        schemas_dir = cls.get_mcp_schemas_dir()

        # 1. Update config files
        modified = False
        for p in cls.get_config_paths():
            real_path = os.path.realpath(p)
            if os.path.isfile(real_path):
                try:
                    with open(real_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    disabled_servers = data.get("disabledMcpServers", {})
                    if server_name in disabled_servers:
                        srv_cfg = disabled_servers.pop(server_name)
                        if "mcpServers" not in data:
                            data["mcpServers"] = {}
                        data["mcpServers"][server_name] = srv_cfg
                        with open(real_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2)
                        modified = True
                except Exception:
                    pass

        # 2. Rename schema directory from .disabled
        disabled_dir = os.path.join(schemas_dir, f"{server_name}.disabled")
        active_dir = os.path.join(schemas_dir, server_name)
        renamed = False
        if os.path.isdir(disabled_dir):
            try:
                os.rename(disabled_dir, active_dir)
                renamed = True
            except Exception:
                pass

        return {
            "success": modified or renamed,
            "server_name": server_name,
            "config_updated": modified,
            "directory_renamed": renamed
        }

    @classmethod
    def format_cli_report(cls) -> str:
        """Formats visual CLI report for agy-tokens doctor."""
        report = cls.audit()
        lines = [
            "\n\033[1m\033[36m⚡ TOKENSAFE ENVIRONMENT AUDIT (CONTEXT TAX DOCTOR) ⚡\033[0m\n",
            f"  \033[1mTotal Base Context Tax:\033[0m ~{report['total_static_tokens_per_turn']:,} tokens on EVERY API turn\n",
            "  \033[1mActive MCP Servers:\033[0m"
        ]

        for name, s in sorted(report["servers"].items()):
            if s["active"]:
                if s["is_bloat"]:
                    status = f"\033[31m\033[1m✖ {name:<18} ({s['tool_count']:>3} tools) [CRITICAL BLOAT: ~{s['est_tokens_per_turn']:,} tok/turn]\033[0m"
                else:
                    status = f"\033[32m✔ {name:<18} ({s['tool_count']:>3} tools) [~{s['est_tokens_per_turn']:,} tok/turn]\033[0m"
            else:
                status = f"\033[2m○ {name:<18} ({s['tool_count']:>3} tools) [Disabled]\033[0m"
            lines.append(f"    {status}")

        lines.append(f"\n  \033[1mInstalled Skills:\033[0m {report['total_skills']} skills (~{report['skills_tokens_per_turn']:,} tokens/turn)")

        if report["has_critical_bloat"]:
            lines.append("\n  \033[33m\033[1mDiagnostic Recommendation:\033[0m")
            for b in report["bloated_servers"]:
                lines.append(f"    ➜ Run: \033[36m./bin/agy-tokens mcp disable {b['name']}\033[0m to save ~{b['est_tokens_per_turn']:,} tokens per turn!")
        else:
            lines.append("\n  \033[32m✔ Environment healthy: No critical MCP bloat detected.\033[0m")

        lines.append("")
        return "\n".join(lines)
