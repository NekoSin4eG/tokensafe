#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PostToolUse
Fires after tool execution.
Fulfills Antigravity PostToolUse contract by returning `{}`.
"""

import sys
import json

def main():
    try:
        # Drain stdin to keep pipes clean
        _ = sys.stdin.read()
    except Exception:
        pass

    # PostToolUse contract expects an empty JSON object {}
    print(json.dumps({}))

if __name__ == "__main__":
    main()
