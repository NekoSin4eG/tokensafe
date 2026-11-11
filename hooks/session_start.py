#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: SessionStart
Fires once when a new session starts. Accepts the same injectSteps step types
as PreInvocation (hooks.md contract) and carries the one-shot context that
used to run inside PreInvocation on every turn — mirroring the SessionStart
pattern of Claude Code / Cursor / Gemini CLI: session-scoped state is injected
once as a persistent systemMessage, not re-sent on every turn.

Injects:
1. TokenSafe protocol directives (persistent systemMessage — replaces the
   per-turn ephemeral reminders and the every-5th-turn re-nudge)
2. Quota / community-limit warning banner (user-facing markdown)
3. Environment bloat warning (heavy MCP servers, oversized rule files)
Also kicks off the background OS update notification check.
"""

import sys
import os
import json

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.metrics import MetricsTracker
from src.license_manager import LicenseManager
from src.environment_auditor import EnvironmentAuditor

PROTOCOL_DIRECTIVES = (
    "[TokenSafe Mandatory Directives]:\n"
    "• Terminal code searches (grep/rg/find on source files) are BLOCKED by TokenSafe Guard.\n"
    "• To search keywords, symbols, or configs: call 'tokensafe:scoped_search(query, scope)'.\n"
    "• To inspect component or function definitions: call 'tokensafe:smart_read_file(path, symbol)'.\n"
    "• To locate dependencies & God Nodes: call 'tokensafe:get_architecture_graph(project_path)'.\n"
    "• To inspect all exported types/interfaces: call 'tokensafe:get_api_surface(project_path)'.\n"
    "• Prefer 'edit_file' over 'write_file' for existing files."
)


def _user_markdown(markdown: str) -> dict:
    """systemMessage step rendered as inline markdown to the user; per the
    hooks.md contract the model sees only the systemMessage text (empty here)."""
    return {
        "systemMessage": {
            "systemMessage": "",
            "metadata": {"user_markdown": markdown},
        }
    }


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({}))
            return
        payload = json.loads(raw_input)
    except Exception:
        print(json.dumps({}))
        return

    # hooks.md: hook CWD is the plugin directory, not the workspace — use the
    # workspacePaths from the payload for project-scoped audits.
    workspace_paths = payload.get("workspacePaths") or []
    workspace_root = workspace_paths[0] if workspace_paths else os.getcwd()

    inject_steps = [
        {"systemMessage": {"systemMessage": PROTOCOL_DIRECTIVES, "metadata": {}}}
    ]

    try:
        tracker = MetricsTracker()
        quota = LicenseManager.check_quota(tracker)
        if quota.get("warning"):
            inject_steps.append(_user_markdown(quota["warning"]))
    except Exception:
        pass

    try:
        from src.version_checker import VersionChecker
        VersionChecker.notify_os_update_background()
    except Exception:
        pass

    try:
        bloat_warning = EnvironmentAuditor.get_bloat_warning(workspace_root=workspace_root)
        if bloat_warning:
            inject_steps.append(_user_markdown(bloat_warning))
    except Exception:
        pass

    print(json.dumps({"injectSteps": inject_steps}))


if __name__ == "__main__":
    main()
