#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: Stop
Fires when the execution loop terminates (hooks.md: payload carries
terminationReason and fullyIdle). Minimal, loop-safe epilogue:

1. fullyIdle → final cloud telemetry flush + metrics retention prune,
   once per session instead of on every PostToolUse.
2. terminationReason == MAX_TOKEN_BUDGET_EXCEEDED → a single 'continue'
   nudge suggesting compaction, guarded by a one-shot metrics event so the
   hook can never ping-pong the loop (mirrors the BUDGET_WARNING dedup).

Any other reason returns {} (allow stop). PostInvocation/force_continue is
deliberately not used — continuation loops need a real use case first.
"""

import sys
import os
import json

HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BUDGET_NUDGE_EVENT = "STOP_BUDGET_NUDGE"

BUDGET_NUDGE_REASON = (
    "[TokenSafe] Context budget exceeded for this session. Before continuing, "
    "reclaim context: summarize the state so far, avoid re-reading files "
    "(use 'tokensafe:smart_read_file' with a symbol), and keep the next step small."
)


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({}))
            return
        payload = json.loads(raw_input)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        print(json.dumps({}))
        return

    conversation_id = str(payload.get("conversationId") or "default_session")
    termination_reason = str(payload.get("terminationReason") or "")
    fully_idle = bool(payload.get("fullyIdle", False))

    response = {}

    try:
        from src.metrics import MetricsTracker
        tracker = MetricsTracker()

        # 1. Turn-end / idle epilogue: cloud sync + idle retention prune.
        try:
            from src.telemetry_sync import TelemetrySync
            TelemetrySync.sync_background(throttle_seconds=0.0 if fully_idle else 15.0)
        except Exception:
            pass
        if fully_idle:
            try:
                tracker.prune()
            except Exception:
                pass

        # 2. One-shot continuation nudge on token-budget exhaustion.
        if termination_reason == "MAX_TOKEN_BUDGET_EXCEEDED":
            already = tracker.get_last_event(conversation_id, BUDGET_NUDGE_EVENT)
            if not already:
                tracker.record_event(
                    session_id=conversation_id,
                    event_type=BUDGET_NUDGE_EVENT,
                    original_tokens=0,
                    optimized_tokens=0,
                    details={"terminationReason": termination_reason},
                )
                response = {"decision": "continue", "reason": BUDGET_NUDGE_REASON}
    except Exception:
        response = {}

    print(json.dumps(response))


if __name__ == "__main__":
    main()
