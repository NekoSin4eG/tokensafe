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

HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.loop_detector import LoopDetector
from src.metrics import MetricsTracker
from src.tokenizer import count_tokens
from src.license_manager import LicenseManager
from src.adapter_core import guard_write


def _trigger_bg_sync():
    try:
        from src.telemetry_sync import TelemetrySync
        TelemetrySync.sync_background(throttle_seconds=20.0)
    except Exception:
        pass


def _resolve_hook_path(raw_path: str, hook_cwd: str) -> str:
    if not raw_path:
        return ""
    if os.path.isabs(raw_path):
        return raw_path
    return os.path.abspath(os.path.join(hook_cwd, raw_path))


def _handle(payload: dict) -> dict:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        tool_call = {}
    tool_name = str(tool_call.get("name") or "")
    args = tool_call.get("args")
    if not isinstance(args, dict):
        args = {}
    conversation_id = str(payload.get("conversationId") or "default_session")
    last_user_input = str(payload.get("lastUserInput") or "")
    # Working directory of the tool call: lets CommandSanitizer peek at
    # package.json and Guard audit git staging in the user's repo (never plugin CWD).
    workspace_paths = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    hook_cwd = str(payload.get("cwd") or (workspace_paths[0] if workspace_paths else "") or os.getcwd())
    try:
        from src.paths import save_active_workspace
        if workspace_paths and workspace_paths[0]:
            save_active_workspace(str(workspace_paths[0]))
        elif hook_cwd:
            save_active_workspace(hook_cwd)
    except Exception:
        pass

    tracker = MetricsTracker()
    quota = LicenseManager.check_quota(tracker)
    is_quota_exceeded = bool(quota.get("is_quota_exceeded", False))

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
        _trigger_bg_sync()
        return {"decision": "force_ask", "reason": loop_msg}

    response: dict = {"decision": "allow"}

    # 2. TokenSafe Guard: Inspect file writes and edits for security violations
    if tool_name in ("write_file", "edit_file"):
        from src.guard import TokenSafeGuard
        raw_path = str(args.get("Path") or args.get("TargetFile") or args.get("path") or "")
        path = _resolve_hook_path(raw_path, hook_cwd)
        code_to_check = args.get("Content", "") if tool_name == "write_file" else args.get("Replacement", "")
        guard_res = TokenSafeGuard.inspect_file_write(path, code_to_check)
        if guard_res.blocked:
            tracker.record_event(
                session_id=conversation_id,
                event_type=f"GUARD_{guard_res.check_name}",
                original_tokens=count_tokens(code_to_check),
                optimized_tokens=0,
                details={
                    "tool": tool_name,
                    "target_file": os.path.basename(path) if path else "unknown",
                    "reason": guard_res.reason
                }
            )
            _trigger_bg_sync()
            return {"decision": "deny", "reason": f"🛡️ TokenSafe Guard: {guard_res.reason}"}

    # 3. Output Guard (shared guard_write, v3.0.0): deny wholesale overwrite of
    #    large existing files to enforce edit_file; ask on small-delta writes.
    if tool_name == "write_file":
        raw_path = str(args.get("Path") or args.get("path") or "")
        path = _resolve_hook_path(raw_path, hook_cwd)
        content = args.get("Content", "")
        is_append = args.get("Append", False)
        verdict = guard_write(path, str(content or ""), bool(is_append), "antigravity", conversation_id)
        if verdict["decision"] in ("deny", "ask"):
            _trigger_bg_sync()
            return {"decision": verdict["decision"], "reason": verdict["reason"]}

    # 3. read_file Optimization (AST Windowing & Skeletonization).
    #    'view_file' is the IDE-surface name for the same tool (hooks.md) —
    #    route it through the same optimization pipeline.
    elif tool_name in ("read_file", "view_file"):
        from src.slicer import ToolSlicer
        from src.skeletonizer import Skeletonizer
        raw_path = str(args.get("Path") or args.get("path") or args.get("FilePath") or "")
        path = _resolve_hook_path(raw_path, hook_cwd)
        start_line = args.get("StartLine")
        if start_line is None:
            start_line = args.get("startLine")
        end_line = args.get("EndLine")
        if end_line is None:
            end_line = args.get("endLine")
        # The overwrite merge is shallow top-level key replacement — we must
        # rewrite the SAME keys the agent actually sent, or a "path"/"FilePath"
        # read would get the optimized content under a brand-new "Path" key
        # while its original argument still points at the full file.
        path_key = next((k for k in ("Path", "path", "FilePath") if k in args), "Path")
        start_key = next((k for k in ("StartLine", "startLine") if k in args), "StartLine")
        end_key = next((k for k in ("EndLine", "endLine") if k in args), "EndLine")

        if is_quota_exceeded:
            # Free tier quota reached: pass through raw read_file without AST skeletonization
            if LicenseManager.should_emit_quota_banner(tracker, conversation_id, every_n_turns=5) and quota.get("warning"):
                response["reason"] = quota["warning"]
            return response

        # Step 3A: If model explicitly specified line range, respect it
        if start_line is not None and end_line is not None:
            return response

        # Step 3B: If a specific symbol/function is targeted in user prompt, check graph-guided skeleton first
        slicer_args = dict(args)
        if path:
            slicer_args[path_key] = path
        overwrite_args, metrics = ToolSlicer.optimize_read_file(slicer_args, context_hint=last_user_input)
        target_sym = metrics.get("target_symbol")
        if target_sym and path and os.path.exists(path):
            skel_path, orig_t, skel_t = Skeletonizer.get_skeleton_file(path, target_symbol=target_sym)
            if skel_path and orig_t > skel_t:
                saved = orig_t - skel_t
                response["overwrite"] = {path_key: skel_path}
                response["reason"] = f"Token Optimizer: Graph-guided AST view for '{target_sym}' (Saved ~{saved} tokens)"
                tracker.record_event(
                    session_id=conversation_id,
                    event_type="GRAPH_SKELETON",
                    original_tokens=orig_t,
                    optimized_tokens=skel_t,
                    details={
                        "target_file": os.path.basename(path),
                        "symbol": target_sym,
                        "saved_tokens": saved
                    }
                )
                return response

        if overwrite_args and metrics.get("saved_tokens", 0) > 0:
            response["overwrite"] = {
                start_key: overwrite_args["StartLine"],
                end_key: overwrite_args["EndLine"],
            }
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
            return response

        # Step 3C: If entire file requested with no symbol target, provide AST Skeleton (mtime-cached)
        if path and os.path.exists(path):
            skeleton_path, orig_t, skel_t = Skeletonizer.get_skeleton_file(path)
            if skeleton_path and orig_t > skel_t:
                saved = orig_t - skel_t
                response["overwrite"] = {path_key: skeleton_path}
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
                return response

    # 4. run_command Optimization & TokenSafe Guard
    elif tool_name == "run_command":
        from src.guard import TokenSafeGuard
        from src.diff_slicer import DiffSlicer
        from src.command_sanitizer import CommandSanitizer
        cmd = str(args.get("CommandLine") or "")
        guard_res = TokenSafeGuard.inspect_command(cmd, cwd=hook_cwd)
        if guard_res.blocked:
            tracker.record_event(
                session_id=conversation_id,
                event_type=f"GUARD_{guard_res.check_name}",
                original_tokens=count_tokens(cmd),
                optimized_tokens=0,
                details={"cmd": cmd[:100], "reason": guard_res.reason}
            )
            _trigger_bg_sync()
            return {"decision": "deny", "reason": f"🛡️ TokenSafe Guard: {guard_res.reason}"}
        elif guard_res.warning_only:
            response["reason"] = f"⚠️ TokenSafe Guard Warning: {guard_res.reason}"

        if is_quota_exceeded:
            # Free tier quota reached: skip Pro diff/command optimization while Security Guards remain active
            if LicenseManager.should_emit_quota_banner(tracker, conversation_id, every_n_turns=5) and quota.get("warning"):
                response["reason"] = response.get("reason") or quota["warning"]
            return response

        # Check AST Diff Slicer first (per-stage rewrite of git diff stages),
        # then run the Command Sanitizer on the result so other stages
        # (git log, pytest, rg, ...) in the same compound command still
        # get their rules applied.
        diff_cmd, diff_saved, diff_reason = DiffSlicer.optimize_git_diff_command(cmd)
        if diff_cmd:
            cmd = diff_cmd
        new_cmd, san_saved, san_reason = CommandSanitizer.sanitize(cmd, cwd=hook_cwd)
        if new_cmd:
            est_saved = san_saved + (diff_saved if diff_cmd else 0)
            reason = (f"{diff_reason}; {san_reason}" if diff_cmd else san_reason)
        elif diff_cmd:
            new_cmd, est_saved, reason = diff_cmd, diff_saved, diff_reason

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

    return response


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"decision": "allow"}))
            return
        payload = json.loads(raw_input)
        if not isinstance(payload, dict):
            print(json.dumps({"decision": "allow"}))
            return
        response = _handle(payload)
        if not isinstance(response, dict):
            response = {"decision": "allow"}
    except Exception:
        # Hooks must always fail open so an internal error never blocks the IDE/agent.
        response = {"decision": "allow"}

    print(json.dumps(response))

if __name__ == "__main__":
    main()
