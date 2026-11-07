#!/usr/bin/env python3
"""
Antigravity Lifecycle Hook: PreInvocation
Fires before the LLM is invoked.
1. Intercepts lastUserInput
2. Compresses it to TOCP Intent IR
3. Records metrics in SQLite
4. Injects compressed context for the model and visual savings badge for the user
"""

import sys
import os
import json

# Ensure project root is in sys.path
HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HOOK_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.compactor import PromptCompactor
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

    if not last_user_input or len(last_user_input.strip()) < 5:
        # Pass through without injection
        print(json.dumps({}))
        return

    # Compact the user prompt
    compaction = PromptCompactor.compact(last_user_input)
    saved_tokens = compaction["saved_tokens"]
    reduction_pct = compaction["reduction_pct"]

    tracker = MetricsTracker()
    inject_steps = []

    # If significant savings achieved (> 5 tokens saved)
    if saved_tokens >= 5:
        tracker.record_event(
            session_id=conversation_id,
            event_type="PROMPT",
            original_tokens=compaction["original_tokens"],
            optimized_tokens=compaction["compact_tokens"],
            details={
                "action": compaction["action"],
                "target_file": compaction["target_file"],
                "symbol": compaction["symbol"],
                "traits": compaction["traits"]
            }
        )

        badge_markdown = (
            f"⚡ **Token Optimizer:** Compaction active — saved **{saved_tokens} tokens** "
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

    response = {}
    if inject_steps:
        response["injectSteps"] = inject_steps

    print(json.dumps(response))

if __name__ == "__main__":
    main()
