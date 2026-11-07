#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreToolUse
Fires before tools (read_file, run_command) execute.
1. Intercepts large read_file operations
2. Applies targeted AST/symbol slicing if the file is massive
3. Overwrites StartLine and EndLine to protect context window
4. Records token savings
"""

import sys
import os
import json

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.slicer import ToolSlicer
from src.metrics import MetricsTracker

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"decision": "allow"}))
            return

        payload = json.loads(raw_input)
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return

    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    conversation_id = payload.get("conversationId", "default_session")
    last_user_input = payload.get("lastUserInput", "")

    response = {"decision": "allow"}

    if tool_name == "read_file":
        overwrite_args, metrics = ToolSlicer.optimize_read_file(args, context_hint=last_user_input)
        if overwrite_args and metrics.get("saved_tokens", 0) > 0:
            response["overwrite"] = overwrite_args
            response["reason"] = f"Token Optimizer: Sliced read window to lines {overwrite_args['StartLine']}-{overwrite_args['EndLine']} (Saved ~{metrics['saved_tokens']} tokens)"

            # Record in metrics
            tracker = MetricsTracker()
            tracker.record_event(
                session_id=conversation_id,
                event_type="TOOL_READ",
                original_tokens=metrics["original_tokens"],
                optimized_tokens=metrics["optimized_tokens"],
                details={
                    "target_file": os.path.basename(args.get("Path", "")),
                    "lines_total": metrics.get("lines_total", 0),
                    "lines_sliced": metrics.get("lines_sliced", 0)
                }
            )

    print(json.dumps(response))

if __name__ == "__main__":
    main()
