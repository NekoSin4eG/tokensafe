"""
History Compactor: Summarizes older multi-turn tool interactions in Antigravity transcripts
into a dense Consolidated State Ledger (CSL), eliminating the quadratic context explosion.
"""

import os
import json
import re
from typing import Dict, Any, Optional, List, Tuple
from src.tokenizer import count_tokens


def _extract_step_text(step: dict) -> str:
    """Step text in any supported transcript format. Antigravity writes a
    top-level `content` string; Claude Code writes `message.content` as a
    string or a list of typed blocks (text / thinking)."""
    if not isinstance(step, dict):
        return ""
    content = step.get("content")
    if isinstance(content, str) and content:
        return content
    message = step.get("message")
    if not isinstance(message, dict):
        return ""
    mc = message.get("content")
    if isinstance(mc, str) and mc:
        return mc
    if isinstance(mc, list):
        parts = []
        for block in mc:
            if isinstance(block, dict) and block.get("type") in ("text", "thinking"):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return " ".join(parts)
    return ""


_READ_TOOLS = {"read_file", "view_file", "Read"}
_EDIT_TOOLS = {"edit_file", "write_file", "Edit", "Write", "NotebookEdit"}
_COMMAND_TOOLS = {"run_command", "Bash"}
_PATH_KEYS = ("Path", "path", "FilePath", "file_path", "notebook_path")
_COMMAND_KEYS = ("CommandLine", "command")


def _arg_value(args: dict, keys) -> str:
    if not isinstance(args, dict):
        return ""
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_step_tool_calls(step: dict) -> List[Tuple[str, dict]]:
    """Tool calls in any supported transcript format: Antigravity
    `tool_calls: [{name, args}]` or Claude Code `message.content` blocks of
    type `tool_use` with `name` / `input`. Returns (name, args) pairs."""
    calls: List[Tuple[str, dict]] = []
    if not isinstance(step, dict):
        return calls
    for tc in step.get("tool_calls") or []:
        if isinstance(tc, dict):
            calls.append((str(tc.get("name", "")), tc.get("args") or {}))
    message = step.get("message")
    if isinstance(message, dict):
        mc = message.get("content")
        if isinstance(mc, list):
            for block in mc:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append((str(block.get("name", "")), block.get("input") or {}))
    return calls

class HistoryCompactor:
    """Folds past conversation tool steps into a compact state ledger."""

    @classmethod
    def measure_transcript(cls, transcript_path: str) -> int:
        """Lightweight transcript mass probe: returns the token count of all
        step content without building a ledger. Used by the Context Budget HUD
        to count REAL history mass on early turns where the full fold is not
        yet active (< 3 turns) and by hooks that only need the estimate."""
        if not transcript_path or not os.path.exists(transcript_path):
            return 0
        total = 0
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        step = json.loads(line)
                    except Exception:
                        continue
                    text = _extract_step_text(step)
                    if text:
                        total += count_tokens(text)
        except Exception:
            return 0
        return total

    @classmethod
    def fold_transcript(cls, transcript_path: str, invocation_num: int) -> Tuple[Optional[str], int, int, Dict[str, Any]]:
        """
        Parses transcriptPath and generates a Consolidated State Ledger if turns >= 3.
        Returns: (consolidated_ledger, orig_history_tokens, folded_tokens, stats_dict)
        """
        if invocation_num < 3 or not transcript_path or not os.path.exists(transcript_path):
            return None, 0, 0, {}

        try:
            steps = []
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            steps.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            return None, 0, 0, {}

        if len(steps) < 4:
            return None, 0, 0, {}

        read_files = set()
        edited_files = set()
        executed_commands = []
        raw_text_accum = []

        for step in steps:
            # Accumulate content for token counting (any supported format)
            text = _extract_step_text(step)
            if text:
                raw_text_accum.append(text)

            # Inspect tool calls (Antigravity tool_calls / Claude Code tool_use)
            for name, args in _extract_step_tool_calls(step):
                if name in _READ_TOOLS:
                    p = _arg_value(args, _PATH_KEYS)
                    if p:
                        read_files.add(os.path.basename(p))
                elif name in _EDIT_TOOLS:
                    p = _arg_value(args, _PATH_KEYS)
                    if p:
                        edited_files.add(os.path.basename(p))
                elif name in _COMMAND_TOOLS:
                    cmd = _arg_value(args, _COMMAND_KEYS)
                    if cmd:
                        # Keep short representation
                        clean_cmd = cmd.split()[0] if cmd.split() else cmd
                        if clean_cmd not in executed_commands:
                            executed_commands.append(clean_cmd)

        raw_history_str = " ".join(raw_text_accum)
        orig_tokens = count_tokens(raw_history_str)

        # Only fold if history has accumulated significant mass (> 1,000 tokens)
        if orig_tokens < 1000:
            return None, 0, 0, {}

        # Build Consolidated State Ledger
        ledger_lines = ["[TOCP State Ledger]:"]
        if read_files:
            ledger_lines.append(f"• Inspected: {', '.join(sorted(read_files))}")
        if edited_files:
            ledger_lines.append(f"• Modified: {', '.join(sorted(edited_files))}")
        if executed_commands:
            ledger_lines.append(f"• Executed: {', '.join(executed_commands[:5])}")

        consolidated_text = "\n".join(ledger_lines)
        folded_tokens = count_tokens(consolidated_text)

        saved_tokens = max(0, orig_tokens - folded_tokens)
        stats = {
            "read_files_count": len(read_files),
            "edited_files_count": len(edited_files),
            "executed_commands_count": len(executed_commands),
            "saved_tokens": saved_tokens,
            "orig_tokens": orig_tokens,
            "folded_tokens": folded_tokens
        }

        return consolidated_text, orig_tokens, folded_tokens, stats
