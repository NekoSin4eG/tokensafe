#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreToolUse
Fires before tools (read_file, run_command) execute.
1. Intercepts read_file:
   - If specific function targeted -> AST line windowing (StartLine/EndLine)
   - If full file requested -> AST Skeletonization (folding method bodies)
2. Intercepts run_command:
   - Sanitizes verbose commands (lockfile diffs, unconstrained logs, test outputs)
3. Records all token savings in SQLite
"""

import sys
import os
import json

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.slicer import ToolSlicer
from src.skeletonizer import Skeletonizer
from src.command_sanitizer import CommandSanitizer
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
    tracker = MetricsTracker()

    # 1. read_file Optimization (AST Windowing & Skeletonization)
    if tool_name == "read_file":
        path = args.get("Path", "")
        start_line = args.get("StartLine")
        end_line = args.get("EndLine")

        # Step 1A: If model explicitly specified line range, respect it
        if start_line is not None and end_line is not None:
            print(json.dumps(response))
            return

        # Step 1B: If a specific symbol/function is targeted in user prompt, slice to it
        overwrite_args, metrics = ToolSlicer.optimize_read_file(args, context_hint=last_user_input)
        if overwrite_args and metrics.get("saved_tokens", 0) > 0:
            response["overwrite"] = overwrite_args
            response["reason"] = (
                f"Token Optimizer: Sliced read window to lines "
                f"{overwrite_args['StartLine']}-{overwrite_args['EndLine']} (Saved ~{metrics['saved_tokens']} tokens)"
            )

            tracker.record_event(
                session_id=conversation_id,
                event_type="TOOL_READ",
                original_tokens=metrics["original_tokens"],
                optimized_tokens=metrics["optimized_tokens"],
                details={
                    "target_file": os.path.basename(path),
                    "lines_total": metrics.get("lines_total", 0),
                    "lines_sliced": metrics.get("lines_sliced", 0)
                }
            )
            print(json.dumps(response))
            return

        # Step 1C: If entire file requested with no symbol target, provide AST Skeleton
        if path and os.path.exists(path):
            skeleton_path, orig_t, skel_t = Skeletonizer.get_skeleton_file(path)
            if skeleton_path and orig_t > skel_t:
                saved = orig_t - skel_t
                response["overwrite"] = {"Path": skeleton_path}
                response["reason"] = (
                    f"Token Optimizer: Provided AST skeleton view (Saved ~{saved} tokens). "
                    f"Call read_file with StartLine/EndLine to inspect specific method bodies."
                )

                tracker.record_event(
                    session_id=conversation_id,
                    event_type="AST_SKELETON",
                    original_tokens=orig_t,
                    optimized_tokens=skel_t,
                    details={
                        "target_file": os.path.basename(path),
                        "saved_tokens": saved
                    }
                )
                print(json.dumps(response))
                return

    # 2. run_command Optimization (Command Sanitizer)
    elif tool_name == "run_command":
        cmd = args.get("CommandLine", "")
        new_cmd, est_saved, reason = CommandSanitizer.sanitize(cmd)
        if new_cmd and est_saved > 0:
            response["overwrite"] = {"CommandLine": new_cmd}
            response["reason"] = f"Token Optimizer: {reason} (Saved ~{est_saved} tokens)"

            tracker.record_event(
                session_id=conversation_id,
                event_type="COMMAND_FILTER",
                original_tokens=est_saved + 50,
                optimized_tokens=50,
                details={
                    "original_cmd": cmd[:50],
                    "sanitized_cmd": new_cmd[:50],
                    "reason": reason
                }
            )

    print(json.dumps(response))

if __name__ == "__main__":
    main()
