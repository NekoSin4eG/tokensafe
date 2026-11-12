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

HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.compactor import PromptCompactor
from src.history_compactor import HistoryCompactor
from src.metrics import MetricsTracker
from src.governor import ContextBudget
from src.license_manager import LicenseManager
from src.adapter_core import TOKENSAFE_DIRECTIVES as PROTOCOL_DIRECTIVES
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

def _handle(payload: dict) -> dict:
    last_user_input = str(payload.get("lastUserInput") or "")
    conversation_id = str(payload.get("conversationId") or "default_session")
    try:
        invocation_num = int(payload.get("invocationNum", 1) or 1)
    except (TypeError, ValueError):
        invocation_num = 1
    transcript_path = str(payload.get("transcriptPath") or "")

    tracker = MetricsTracker()
    inject_steps = []

    # 0. Asynchronous telemetry sync (detached & throttled to 20s)
    try:
        from src.telemetry_sync import TelemetrySync
        TelemetrySync.sync_background(throttle_seconds=20.0)
    except Exception:
        pass

    # 1. Quota check gates compaction tiers. Its warning banner is injected
    #    once per session by session_start.py, not on every turn here.
    quota = LicenseManager.check_quota(tracker)

    # 2. Prompt Compaction (Paused completely once 500k Community measured quota is reached).
    #    Skipped while the proxy is in the loop: it already replaced the prompt.
    is_quota_exceeded = bool(quota.get("is_quota_exceeded", False))
    if is_quota_exceeded:
        if LicenseManager.should_emit_quota_banner(tracker, conversation_id, every_n_turns=5) and quota.get("warning"):
            inject_steps.append({
                "systemMessage": {
                    "systemMessage": "",
                    "metadata": {
                        "user_markdown": quota["warning"]
                    }
                }
            })
    elif last_user_input and len(last_user_input.strip()) >= 5 and not _proxy_is_active():
        compaction = PromptCompactor.compact(last_user_input, enable_distill=True)
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
    #    Only inject into context on first fold or >= 1500 token delta so
    #    intermediate turns do not churn prefix tokens unnecessarily.
    folded_text = None
    fresh_fold = False
    orig_hist_tokens = 0
    if not is_quota_exceeded and invocation_num >= 3 and transcript_path:
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
            if prev_event is None or delta_orig >= 1500:
                fresh_fold = True
                inc_orig = orig_hist_tokens if prev_event is None else delta_orig
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

    # 3b. Real history mass for the Context Budget HUD
    history_mass = 0
    if folded_text is not None:
        history_mass = orig_hist_tokens
    elif transcript_path:
        try:
            history_mass = HistoryCompactor.measure_transcript(transcript_path)
        except Exception:
            history_mass = 0

    # 4. Adaptive Protocol Enforcement Reminder (Guard-triggered or fresh fold only)
    recent_guard = tracker.get_last_event(conversation_id, "GUARD_RAW_CODE_SEARCH")
    guard_unreminded = False
    if isinstance(recent_guard, dict):
        last_remind = tracker.get_last_event(conversation_id, "PROTOCOL_REMINDER")
        is_valid_remind = isinstance(last_remind, dict) and last_remind.get("event_type") == "PROTOCOL_REMINDER"
        if not is_valid_remind:
            guard_unreminded = True
        else:
            rem_details = last_remind.get("details")
            rem_dict = rem_details if isinstance(rem_details, dict) else {}
            try:
                reminded_id = int(rem_dict.get("guard_id", 0) or 0)
                recent_id = int(recent_guard.get("id", 0) or 0)
            except (TypeError, ValueError):
                reminded_id = 0
                recent_id = 0
            guard_unreminded = recent_id > reminded_id
    if fresh_fold or guard_unreminded:
        inject_steps.append({
            "systemMessage": {
                "systemMessage": PROTOCOL_DIRECTIVES,
                "metadata": {}
            }
        })
        if guard_unreminded and recent_guard.get("id") is not None:
            try:
                tracker.record_event(
                    session_id=conversation_id,
                    event_type="PROTOCOL_REMINDER",
                    original_tokens=0,
                    optimized_tokens=0,
                    details={"guard_id": int(recent_guard["id"])},
                )
            except Exception:
                pass

    # 5. Context Budget HUD warning
    try:
        budget = ContextBudget.estimate(
            tracker, session_id=conversation_id,
            model=_model_name(payload),
            history_tokens=history_mass,
            transcript_path=transcript_path
        )
        if budget["used_pct"] >= 80 and budget["instrumented_events"] >= 3:
            already = tracker.get_last_event(conversation_id, "BUDGET_WARNING")
            if not already:
                if transcript_path:
                    action = (f"Call tokensafe:compact_conversation(transcript_path='{transcript_path}') "
                              f"now to fold past turns before continuing.")
                else:
                    action = budget["recommendation"]
                warn = (
                    f"🧠 **Context Budget:** ~{budget['used_pct']}% of the estimated window used "
                    f"({budget['used_estimated']:,}/{budget['window_tokens']:,} tokens). {action}"
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

    return {"injectSteps": inject_steps} if inject_steps else {}


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({}))
            return
        payload = json.loads(raw_input)
        if not isinstance(payload, dict):
            print(json.dumps({}))
            return
        response = _handle(payload)
        if not isinstance(response, dict):
            response = {}
    except Exception:
        # Fail-open: never block user prompt submission on internal/SQLite errors
        response = {}

    print(json.dumps(response))

if __name__ == "__main__":
    main()
