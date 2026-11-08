#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreToolUse
1. Loop Guard: Detects identical repeated tool calls and triggers force_ask
2. Output Guard: Blocks write_file on existing large files, enforcing surgical edit_file
3. AST Skeletonizer & Line Slicer: Protects read_file operations
4. Command Sanitizer: Cleans up git diff, git log, pytest, find, grep
5. Records all savings in SQLite
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
from src.loop_detector import LoopDetector
from src.metrics import MetricsTracker
from src.tokenizer import count_tokens

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

    tracker = MetricsTracker()

    # 1. Loop Detector Guard (Applies to all tools)
    is_loop, repeat_count, loop_msg = LoopDetector.check_loop(conversation_id, tool_name, args)
    if is_loop:
        tracker.record_event(
            session_id=conversation_id,
            event_type="LOOP_GUARD",
            original_tokens=5000,
            optimized_tokens=100,
            details={"tool": tool_name, "repeats": repeat_count}
        )
        print(json.dumps({
            "decision": "force_ask",
            "reason": loop_msg
        }))
        return

    response = {"decision": "allow"}

    # 2. Output Guard: Block write_file on large existing files to enforce edit_file
    if tool_name == "write_file":
        path = args.get("Path", "")
        content = args.get("Content", "")
        is_append = args.get("Append", False)

        if not is_append and path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    existing_lines = len(f.readlines())
            except Exception:
                existing_lines = 0

            # If file already has >= 40 lines, block wholesale overwrite to save output tokens
            if existing_lines >= 40:
                content_tokens = count_tokens(content)
                tracker.record_event(
                    session_id=conversation_id,
                    event_type="OUTPUT_GUARD",
                    original_tokens=content_tokens,
                    optimized_tokens=80, # typical edit_file diff tokens
                    details={
                        "target_file": os.path.basename(path),
                        "existing_lines": existing_lines,
                        "saved_output_tokens": max(0, content_tokens - 80)
                    }
                )

                print(json.dumps({
                    "decision": "deny",
                    "reason": (
                        f"Token Optimizer Output Guard: '{os.path.basename(path)}' already exists with {existing_lines} lines. "
                        f"Wholesale rewriting via write_file burns expensive output tokens. "
                        f"Please use 'edit_file' with surgical Target and Replacement blocks instead."
                    )
                }))
                return

    # 3. read_file Optimization (AST Windowing & Skeletonization)
    elif tool_name == "read_file":
        path = args.get("Path", "")
        start_line = args.get("StartLine")
        end_line = args.get("EndLine")

        # Step 3A: If model explicitly specified line range, respect it
        if start_line is not None and end_line is not None:
            print(json.dumps(response))
            return

        # Step 3B: If a specific symbol/function is targeted in user prompt, slice to it
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

        # Step 3C: If entire file requested with no symbol target, provide AST Skeleton (mtime-cached)
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

    # 4. run_command Optimization (Command Sanitizer)
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
