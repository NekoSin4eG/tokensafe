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

from src.paths import resolve_data_dir

class LoopDetector:
    """Tracks tool invocation signatures to detect hallucination loops."""

    _DEFAULT_STATE_FILE = os.path.join(resolve_data_dir(), "loop_state.json")
    STATE_FILE = _DEFAULT_STATE_FILE
    MAX_IDENTICAL_REPEATS = 3
    SESSION_TTL_SECONDS = 48 * 3600  # prune sessions idle for more than 2 days
    MAX_SESSIONS = 2000

    @classmethod
    def _state_file(cls) -> str:
        # Honor dynamic TOKENSAFE_DATA_DIR changes unless a test explicitly overrode STATE_FILE
        if cls.STATE_FILE != cls._DEFAULT_STATE_FILE:
            return cls.STATE_FILE
        env_dir = os.environ.get("TOKENSAFE_DATA_DIR")
        if env_dir:
            return os.path.join(env_dir, "loop_state.json")
        return cls.STATE_FILE

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
        Uses fcntl on POSIX and msvcrt on Windows.
        """
        state_file = cls._state_file()
        os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
        lock_path = state_file + ".lock"
        handle = open(lock_path, "a+")
        locked_posix = False
        locked_win = False
        try:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked_posix = True
            except ImportError:
                try:
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    locked_win = True
                except Exception:
                    pass
            yield
        finally:
            try:
                if locked_posix:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif locked_win:
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            handle.close()

    @classmethod
    def _load_state(cls) -> Dict[str, Any]:
        state_file = cls._state_file()
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _save_state(cls, state: Dict[str, Any]):
        state_file = cls._state_file()
        try:
            parent = os.path.dirname(state_file) or "."
            os.makedirs(parent, exist_ok=True)
            tmp_path = f"{state_file}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp_path, state_file)
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

            # Prune stale or corrupted sessions so the state file cannot grow unboundedly
            now = time.time()
            stale = []
            for cid, s in state.items():
                if not isinstance(s, dict):
                    stale.append(cid)
                    continue
                try:
                    updated = float(s.get("updated", 0) or 0)
                except (TypeError, ValueError):
                    stale.append(cid)
                    continue
                if (now - updated) > cls.SESSION_TTL_SECONDS:
                    stale.append(cid)
            for cid in stale:
                state.pop(cid, None)

            if len(state) > cls.MAX_SESSIONS:
                ordered = sorted(state.items(), key=lambda kv: float((kv[1] or {}).get("updated", 0) or 0), reverse=True)
                state = dict(ordered[: cls.MAX_SESSIONS])

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
        """Clears state for a conversation safely under lock."""
        if not os.path.exists(cls._state_file()):
            return
        with cls._locked_state():
            state = cls._load_state()
            if conversation_id in state:
                del state[conversation_id]
                cls._save_state(state)
