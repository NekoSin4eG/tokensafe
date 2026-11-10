"""
Anti-Stuck Loop Detector: Identifies repeated identical tool executions
and triggers confirmation to prevent infinite token drain loops.
"""

import os
import json
import time
import hashlib
from contextlib import contextmanager
from typing import Dict, Any, Tuple

class LoopDetector:
    """Tracks tool invocation signatures to detect hallucination loops."""

    STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "loop_state.json")
    MAX_IDENTICAL_REPEATS = 3
    SESSION_TTL_SECONDS = 48 * 3600  # prune sessions idle for more than 2 days

    @classmethod
    def _get_call_signature(cls, tool_name: str, args: Dict[str, Any]) -> str:
        """Computes deterministic hash of tool call arguments."""
        args_str = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(f"{tool_name}:{args_str}".encode("utf-8")).hexdigest()[:16]

    @classmethod
    @contextmanager
    def _locked_state(cls):
        """
        Exclusive read-modify-write guard for the shared state file.
        Hooks for parallel tool calls run as concurrent processes; without the lock
        their increments overwrote each other and identical calls went undetected.
        Uses fcntl on POSIX and degrades to lock-free (best effort) elsewhere.
        """
        os.makedirs(os.path.dirname(cls.STATE_FILE), exist_ok=True)
        lock_path = cls.STATE_FILE + ".lock"
        handle = open(lock_path, "w")
        locked = False
        try:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            except ImportError:
                pass
            yield
        finally:
            try:
                if locked:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()

    @classmethod
    def _load_state(cls) -> Dict[str, Any]:
        if os.path.exists(cls.STATE_FILE):
            try:
                with open(cls.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @classmethod
    def _save_state(cls, state: Dict[str, Any]):
        try:
            with open(cls.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass

    @classmethod
    def check_loop(cls, conversation_id: str, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, int, str]:
        """
        Checks if the tool call is stuck in an identical repeat loop.
        Returns: (is_loop_detected, repeat_count, warning_message)
        """
        if not conversation_id or not tool_name:
            return False, 0, ""

        signature = cls._get_call_signature(tool_name, args)

        with cls._locked_state():
            state = cls._load_state()

            # Prune stale sessions so the state file cannot grow unboundedly
            now = time.time()
            stale = [cid for cid, s in state.items()
                     if not isinstance(s, dict) or (now - float(s.get("updated", 0))) > cls.SESSION_TTL_SECONDS]
            for cid in stale:
                state.pop(cid, None)

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
            session_state["updated"] = now

            state[conversation_id] = session_state
            cls._save_state(state)

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
