#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreInvocation
Fires before the LLM is invoked.
1. Intercepts lastUserInput and compresses it to TOCP Intent IR
2. Folds multi-turn history into Consolidated State Ledger if turns >= 3
3. Records metrics in SQLite
4. Injects compressed context and visual savings badge
"""

import sys
import os
import json

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.compactor import PromptCompactor
from src.history_compactor import HistoryCompactor
from src.metrics import MetricsTracker

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

    last_user_input = payload.get("lastUserInput", "")
    conversation_id = payload.get("conversationId", "default_session")
    invocation_num = payload.get("invocationNum", 1)
    transcript_path = payload.get("transcriptPath", "")

    tracker = MetricsTracker()
    inject_steps = []

    # 1. Prompt Compaction
    if last_user_input and len(last_user_input.strip()) >= 5:
        compaction = PromptCompactor.compact(last_user_input)
        saved_tokens = compaction["saved_tokens"]
        reduction_pct = compaction["reduction_pct"]

        if saved_tokens >= 5:
            tier = compaction.get("tier", "REGEX")
            tracker.record_event(
                session_id=conversation_id,
                event_type="PROMPT",
                original_tokens=compaction["original_tokens"],
                optimized_tokens=compaction["compact_tokens"],
                details={
                    "action": compaction["action"],
                    "target_file": compaction["target_file"],
                    "symbol": compaction["symbol"],
                    "traits": compaction["traits"],
                    "tier": tier
                }
            )

            badge_markdown = (
                f"⚡ **Token Optimizer:** Compaction active ({tier}) — saved **{saved_tokens} tokens** "
                f"(-{reduction_pct}%) on this prompt."
            )

            inject_steps.append({
                "systemMessage": {
                    "systemMessage": f"[TOCP Compressed Intent]: {compaction['compact_text']}",
                    "metadata": {
                        "user_markdown": badge_markdown
                    }
                }
            })

    # 2. Multi-turn History Compaction
    if invocation_num >= 3 and transcript_path:
        folded_text, orig_hist_tokens, folded_tokens, stats = HistoryCompactor.fold_transcript(
            transcript_path, invocation_num
        )
        if folded_text and stats.get("saved_tokens", 0) > 0:
            # Prevent quadratic double-counting: only record incremental new history mass
            prev_event = tracker.get_last_event(session_id=conversation_id, event_type="HISTORY_FOLD")
            prev_orig = 0
            if prev_event and isinstance(prev_event.get("details"), dict):
                prev_orig = prev_event["details"].get("total_accum_orig", 0) or prev_event.get("original_tokens", 0)

            delta_orig = max(0, orig_hist_tokens - prev_orig)
            # Record first fold or incremental accumulation when delta >= 1500 tokens
            if prev_event is None or delta_orig >= 1500:
                inc_orig = orig_hist_tokens if prev_event is None else delta_orig
                inc_saved = max(0, inc_orig - folded_tokens)
                tracker.record_event(
                    session_id=conversation_id,
                    event_type="HISTORY_FOLD",
                    original_tokens=inc_orig,
                    optimized_tokens=folded_tokens,
                    details={
                        "turns": invocation_num,
                        "total_accum_orig": orig_hist_tokens,
                        "read_files": stats.get("read_files_count", 0),
                        "edited_files": stats.get("edited_files_count", 0)
                    }
                )

            hist_badge = (
                f"⚡ **History Compactor:** Stabilized {orig_hist_tokens:,} tokens of previous turns — "
                f"saved **{stats['saved_tokens']:,} tokens**."
            )

            inject_steps.append({
                "systemMessage": {
                    "systemMessage": folded_text,
                    "metadata": {
                        "user_markdown": hist_badge
                    }
                }
            })

    response = {}
    if inject_steps:
        response["injectSteps"] = inject_steps

    print(json.dumps(response))

if __name__ == "__main__":
    main()
