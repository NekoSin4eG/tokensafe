#!/usr/bin/env python3
"""
TokenSafe adapter for Claude Code lifecycle hooks.

Registered via ~/.claude/settings.json:
  UserPromptSubmit -> compacts the prompt and INJECTS the TOCP form as
                     additionalContext (verified inject point in the
                     official Claude Code hooks schema).
  PreToolUse (Bash) -> denies secret-bearing / destructive commands,
                     asks on TLS-verification bypasses.

Contract: stdin = one JSON payload, stdout = one JSON response.
Fail-open: any internal error emits `{}` (Claude treats empty stdout as allow).
"""

import sys
import json
import os

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.adapter_core import TOKENSAFE_DIRECTIVES, compact_for_injection, guard_command

AGENT = "claude_code"


def handle(event: str, payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "") or f"hook_{AGENT}"

    if event == "SessionStart":
        # One-shot session injection (mirrors the Antigravity SessionStart
        # hook): persistent directives instead of per-turn re-injection.
        # No matcher on `source` — inject for startup/resume/clear/compact.
        # Well under the 10k-char additionalContext cap.
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": TOKENSAFE_DIRECTIVES,
            }
        }

    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        context, meta = compact_for_injection(prompt, AGENT, session_id)
        if context and meta.get("compacted"):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        f"{context}\n"
                        f"(TokenSafe: compacted ~{meta['saved_tokens']} prompt tokens, "
                        f"-{meta.get('reduction_pct', 0)}% — original intent preserved.)"
                    ),
                }
            }
        return {}

    if event == "PreToolUse":
        tool = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input") or {}
        if tool in ("Bash", "Shell", "bash"):
            command = str(tool_input.get("command") or "")
            verdict = guard_command(command, AGENT, session_id)
            if verdict["decision"] == "deny":
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": verdict["reason"],
                    }
                }
            if verdict["decision"] == "ask":
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": verdict["reason"],
                    }
                }
        return {}

    return {}


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        print(json.dumps({}))
        return
    event = str(payload.get("hook_event_name") or "")
    try:
        response = handle(event, payload if isinstance(payload, dict) else {})
    except Exception:
        response = {}
    print(json.dumps(response))


if __name__ == "__main__":
    main()
