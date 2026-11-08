"""
Anti-Stuck Loop Detector: Identifies repeated identical tool executions
and triggers confirmation to prevent infinite token drain loops.
"""

import os
import json
import hashlib
from typing import Dict, Any, Tuple

class LoopDetector:
    """Tracks tool invocation signatures to detect hallucination loops."""

    STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "loop_state.json")
    MAX_IDENTICAL_REPEATS = 3

    @classmethod
    def _get_call_signature(cls, tool_name: str, args: Dict[str, Any]) -> str:
        """Computes deterministic hash of tool call arguments."""
        args_str = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(f"{tool_name}:{args_str}".encode("utf-8")).hexdigest()[:16]

    @classmethod
    def check_loop(cls, conversation_id: str, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, int, str]:
        """
        Checks if the tool call is stuck in an identical repeat loop.
        Returns: (is_loop_detected, repeat_count, warning_message)
        """
        if not conversation_id or not tool_name:
            return False, 0, ""

        signature = cls._get_call_signature(tool_name, args)
        state = {}

        if os.path.exists(cls.STATE_FILE):
            try:
                with open(cls.STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}

        session_state = state.get(conversation_id, {
            "last_sig": "",
            "repeat_count": 0,
            "tool_name": ""
        })

        if session_state.get("last_sig") == signature:
            session_state["repeat_count"] = session_state.get("repeat_count", 0) + 1
        else:
            session_state["last_sig"] = signature
            session_state["repeat_count"] = 1
            session_state["tool_name"] = tool_name

        state[conversation_id] = session_state

        # Save state
        try:
            os.makedirs(os.path.dirname(cls.STATE_FILE), exist_ok=True)
            with open(cls.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass

        repeats = session_state["repeat_count"]
        if repeats >= cls.MAX_IDENTICAL_REPEATS:
            msg = (
                f"Token Optimizer Loop Guard: Tool '{tool_name}' invoked with identical "
                f"parameters {repeats} times in a row. Pausing loop to prevent token drain."
            )
            return True, repeats, msg

        return False, repeats, ""

    @classmethod
    def reset_session(cls, conversation_id: str):
        """Clears state for a conversation."""
        if os.path.exists(cls.STATE_FILE):
            try:
                with open(cls.STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if conversation_id in state:
                    del state[conversation_id]
                    with open(cls.STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(state, f)
            except Exception:
                pass
