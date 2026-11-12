#!/usr/bin/env python3
"""
TokenSafe adapter for Cursor agent hooks (~/.cursor/hooks.json).

Honest capability matrix (per official Cursor hooks docs):
  beforeShellExecution -> allow/deny/ask: blocks secret-bearing,
                          destructive and TLS-bypassing commands.
  beforeReadFile       -> allow/deny: blocks lockfile/bundle/dist dumps
                          that would burn ~50k context tokens.
  beforeSubmitPrompt   -> allow/deny ONLY (no injection in Cursor yet):
                          records nothing, always allows. Kept as a no-op
                          so the hook wiring is future-proof.
  stop / afterFileEdit -> observational no-ops.

IMPORTANT: Cursor BLOCKS the action when a hook prints invalid JSON.
Fail-open here means printing a valid per-event ALLOW response from the
exception handler, never an empty string.
"""

import sys
import json
import os

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.adapter_core import guard_command, guard_read

AGENT = "cursor"


def _allow(event: str) -> dict:
    if event == "beforeSubmitPrompt":
        return {"continue": True}
    return {"permission": "allow"}


def handle(event: str, payload: dict) -> dict:
    session_id = str(payload.get("conversation_id") or payload.get("session_id") or "") or f"hook_{AGENT}"

    if event == "beforeShellExecution":
        command = str(payload.get("command") or "")
        verdict = guard_command(command, AGENT, session_id)
        if verdict["decision"] == "deny":
            return {"permission": "deny", "user_message": verdict["reason"]}
        if verdict["decision"] == "ask":
            return {"permission": "ask", "agent_message": verdict["reason"]}
        return {"permission": "allow"}

    if event == "beforeReadFile":
        file_path = str(payload.get("file_path") or "")
        verdict = guard_read(file_path, AGENT, session_id)
        if verdict["decision"] == "deny":
            return {"permission": "deny", "user_message": verdict["reason"]}
        return {"permission": "allow"}

    if event == "beforeSubmitPrompt":
        # Cursor cannot inject context (allow/deny only). Nothing honest to
        # record: the model still sees the full prompt. Always allow.
        return {"continue": True}

    return _allow(event)


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
        response = _allow(event)
    if not isinstance(response, dict) or not response:
        response = _allow(event)
    if event in ("beforeShellExecution", "beforeReadFile", "beforeSubmitPrompt", "stop"):
        try:
            from src.telemetry_sync import TelemetrySync
            TelemetrySync.sync_background(throttle_seconds=0.0 if event == "stop" else 20.0)
        except Exception:
            pass
    print(json.dumps(response))


if __name__ == "__main__":
    main()
