"""
History Compactor: Summarizes older multi-turn tool interactions in Antigravity transcripts
into a dense Consolidated State Ledger (CSL), eliminating the quadratic context explosion.
"""

import os
import json
import re
from typing import Dict, Any, Optional, List, Tuple
from src.tokenizer import count_tokens

class HistoryCompactor:
    """Folds past conversation tool steps into a compact state ledger."""

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
            # Accumulate content for token counting
            content = step.get("content", "")
            if content:
                raw_text_accum.append(str(content))

            # Inspect tool calls
            tool_calls = step.get("tool_calls", [])
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    name = tc.get("name", "")
                    args = tc.get("args", {})
                    if name == "read_file":
                        p = args.get("Path", "")
                        if p:
                            read_files.add(os.path.basename(p))
                    elif name in ("edit_file", "write_file"):
                        p = args.get("Path", "")
                        if p:
                            edited_files.add(os.path.basename(p))
                    elif name == "run_command":
                        cmd = args.get("CommandLine", "")
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
