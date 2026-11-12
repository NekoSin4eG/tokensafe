#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PostToolUse
Fires after tool execution.
1. Fulfills Antigravity PostToolUse contract by returning `{}`.
2. Triggers detached background telemetry sync if pending events exist.
"""

import os
import sys
import json

import time

HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def _should_skip_fast() -> bool:
    if os.environ.get("TOKENSAFE_TELEMETRY_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        data_dir = os.environ.get("TOKENSAFE_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".tokensafe", "data")
        state_file = os.path.join(data_dir, "sync_state.json")
        if os.path.isfile(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict) and (time.time() - float(st.get("last_attempt_at", 0) or 0)) < 20.0:
                return True
    except Exception:
        pass
    return False

def main():
    try:
        # Drain stdin to keep pipes clean
        _ = sys.stdin.read()
    except Exception:
        pass

    # Non-blocking telemetry sync trigger (detached background process)
    if not _should_skip_fast():
        try:
            from src.telemetry_sync import TelemetrySync
            TelemetrySync.sync_background(throttle_seconds=20.0)
        except Exception:
            pass

    # PostToolUse contract expects an empty JSON object {}
    print(json.dumps({}))

if __name__ == "__main__":
    main()
