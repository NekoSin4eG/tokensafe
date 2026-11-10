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

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def main():
    try:
        # Drain stdin to keep pipes clean
        _ = sys.stdin.read()
    except Exception:
        pass

    # Non-blocking telemetry sync trigger (detached background process)
    try:
        from src.telemetry_sync import TelemetrySync
        TelemetrySync.sync_background(throttle_seconds=60.0)
    except Exception:
        pass

    # PostToolUse contract expects an empty JSON object {}
    print(json.dumps({}))

if __name__ == "__main__":
    main()
