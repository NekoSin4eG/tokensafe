#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreInvocation
Fires before the LLM is invoked.
1. Intercepts lastUserInput and compresses it to TOCP Intent IR
2. Folds multi-turn history into Consolidated State Ledger if turns >= 3
3. Records metrics in SQLite
4. Injects compressed context and visual savings badge

One-shot session concerns (quota/bloat banners, protocol directives, version
check) live in session_start.py per the SessionStart hook contract — this
hook stays per-turn lean.
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
from src.governor import ContextBudget
from src.license_manager import LicenseManager
from src.paths import resolve_data_dir

def _model_name(payload: dict) -> str:
    """hooks.md contract: all payload keys are camelCase, the model arrives as
    'modelName'. Tolerate the legacy 'model' key and env fallbacks."""
    return (
        payload.get("modelName")
        or payload.get("model")
        or os.environ.get("AGY_MODEL")
        or os.environ.get("MODEL")
        or ""
    )

def _proxy_is_active() -> bool:
    """True when the local tokensafe-proxy recently served agent traffic.

    The proxy REPLACES the last user message with its TOCP form (real
    replace-mode); injecting a second compressed copy from the hook would
    duplicate the optimization and double-count it in metrics."""
    try:
        import time as _time
        marker = os.path.join(resolve_data_dir(), ".proxy_active")
        if os.path.exists(marker):
            return _time.time() - os.path.getmtime(marker) < 120.0
    except Exception:
        pass
    return False

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

    # 1. Quota check gates compaction tiers. Its warning banner is injected
    #    once per session by session_start.py, not on every turn here.
    quota = LicenseManager.check_quota(tracker)

    # 2. Prompt Compaction (Tier 2 LLM Distillation paused if community quota exceeded).
    #    Skipped while the proxy is in the loop: it already replaced the prompt.
    if last_user_input and len(last_user_input.strip()) >= 5 and not _proxy_is_active():
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
                    "tier": tier,
                    "model": _model_name(payload) or "gemini-3.8-flash"
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

    # 3. Multi-turn History Compaction (Paused when community quota is exceeded)
    folded_text = None
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
                        "edited_files": stats.get("edited_files_count", 0),
                        "model": _model_name(payload) or "gemini-3.8-flash"
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

    # 4. Adaptive Protocol Enforcement Reminder (Guard-triggered only).
    #    Full directives are injected once per session by session_start.py as a
    #    persistent systemMessage; here they are re-shown only when the guard
    #    just fired or fresh history was folded.
    recent_guard = tracker.get_last_event(conversation_id, "GUARD_RAW_CODE_SEARCH")
    if folded_text is not None or recent_guard is not None:
        protocol_reminder = (
            "[TokenSafe Mandatory Directives]:\n"
            "• Terminal code searches (grep/rg/find on source files) are BLOCKED by TokenSafe Guard.\n"
            "• To search keywords, symbols, or configs: call 'tokensafe:scoped_search(query, scope)'.\n"
            "• To inspect component or function definitions: call 'tokensafe:smart_read_file(path, symbol)'.\n"
            "• To locate dependencies & God Nodes: call 'tokensafe:get_architecture_graph(project_path)'.\n"
            "• To inspect all exported types/interfaces: call 'tokensafe:get_api_surface(project_path)'.\n"
            "• Prefer 'edit_file' over 'write_file' for existing files."
        )
        inject_steps.append({
            "ephemeralMessage": protocol_reminder
        })

    # 5. Context Budget HUD warning: inject once per session when the
    #    instrumented context crosses 80% of the estimated window.
    try:
        budget = ContextBudget.estimate(
            tracker, session_id=conversation_id,
            model=_model_name(payload)
        )
        if budget["used_pct"] >= 80 and budget["instrumented_events"] >= 3:
            already = tracker.get_last_event(conversation_id, "BUDGET_WARNING")
            if not already:
                warn = (
                    f"🧠 **Context Budget:** ~{budget['used_pct']}% of the estimated window used "
                    f"({budget['used_estimated']:,}/{budget['window_tokens']:,} tokens, instrumented floor). "
                    f"{budget['recommendation']}"
                )
                inject_steps.append({
                    "systemMessage": {
                        "systemMessage": "",
                        "metadata": {"user_markdown": warn}
                    }
                })
                tracker.record_event(
                    session_id=conversation_id,
                    event_type="BUDGET_WARNING",
                    original_tokens=0,
                    optimized_tokens=0,
                    details={"used_pct": budget["used_pct"], "window": budget["window_tokens"]}
                )
    except Exception:
        pass

    response = {}
    if inject_steps:
        response["injectSteps"] = inject_steps

    print(json.dumps(response))

if __name__ == "__main__":
    main()
