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
from src.environment_auditor import EnvironmentAuditor
from src.license_manager import LicenseManager

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

    # 0. Asynchronous telemetry sync (detached & throttled to 60s)
    try:
        from src.telemetry_sync import TelemetrySync
        TelemetrySync.sync_background(throttle_seconds=60.0)
    except Exception:
        pass

    # 1. Quota and Environment Audits (Fires on first invocation)
    quota = LicenseManager.check_quota(tracker)
    if invocation_num <= 1:
        try:
            from src.version_checker import VersionChecker
            VersionChecker.notify_os_update_background()
        except Exception:
            pass
        if quota.get("warning"):
            inject_steps.append({
                "systemMessage": {
                    "systemMessage": "",
                    "metadata": {
                        "user_markdown": quota["warning"]
                    }
                }
            })
        try:
            bloat_warning = EnvironmentAuditor.get_bloat_warning()
            if bloat_warning:
                inject_steps.append({
                    "systemMessage": {
                        "systemMessage": "",
                        "metadata": {
                            "user_markdown": bloat_warning
                        }
                    }
                })
        except Exception:
            pass

    # 1. Prompt Compaction (Tier 2 LLM Distillation paused if community quota exceeded)
    if last_user_input and len(last_user_input.strip()) >= 5:
        enable_distill = not quota.get("is_quota_exceeded", False)
        compaction = PromptCompactor.compact(last_user_input, enable_deepseek=enable_distill)
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

            if quota.get("is_quota_exceeded"):
                badge_markdown = (
                    f"⚡ **TokenSafe:** Saved **{saved_tokens} tokens** via local regex "
                    f"(Community 500k quota reached — [Upgrade to Solo](https://tokensafe.dev/#pricing) for unlimited deep distillation & history folding)."
                )
            else:
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

    # 2. Multi-turn History Compaction (Paused when community quota is exceeded)
    if not quota.get("is_quota_exceeded", False) and invocation_num >= 3 and transcript_path:
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

    # 3. Protocol Enforcement Reminder (Injected ephemerally for the model on every invocation)
    protocol_reminder = (
        "[TokenSafe Mandatory Directives]:\n"
        "• Terminal code searches (grep/rg/find on source files) are BLOCKED by TokenSafe Guard.\n"
        "• To inspect component or function definitions: call 'tokensafe:smart_read_file(path, symbol)'.\n"
        "• To locate dependencies & God Nodes: call 'tokensafe:get_architecture_graph(project_path)'.\n"
        "• To inspect all exported types/interfaces: call 'tokensafe:get_api_surface(project_path)'.\n"
        "• Prefer 'edit_file' over 'write_file' for existing files."
    )
    inject_steps.append({
        "ephemeralMessage": protocol_reminder
    })

    response = {}
    if inject_steps:
        response["injectSteps"] = inject_steps

    print(json.dumps(response))

if __name__ == "__main__":
    main()
