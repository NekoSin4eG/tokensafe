"""
TokenSafe Context Window Governor & Epoch Rollover Engine.
Operates in Append-Only Epoch mode by default so historical turns remain
byte-identical for LLM KV-cache hits until context reaches 80% of the model
window, at which point an atomic Epoch Rollover consolidates deep history
into a Consolidated State Ledger.
"""

import os
import re
import json
from typing import Dict, List, Any, Tuple, Optional
from src.tokenizer import count_tokens
from src.skeletonizer import Skeletonizer
from src.compactor import PromptCompactor
from src.metrics import MetricsTracker


class ContextGovernor:
    """Monitors and enforces context ceilings with cache-safe Append-Only Epochs."""

    EPOCH_WINDOW_RATIO = 0.80
    DEFAULT_MAX_TURN_TOKENS = int(os.environ.get("TOKENSAFE_MAX_TURN_TOKENS", "16000"))
    DEFAULT_TARGET_TURN_TOKENS = int(os.environ.get("TOKENSAFE_TARGET_TURN_TOKENS", "10000"))

    CODE_BLOCK_REGEX = re.compile(r"```([a-zA-Z0-9_\-+]*)\n(.*?)```", re.DOTALL)

    @classmethod
    def resolve_epoch_limit(cls, model: str = "", window_tokens: Optional[int] = None) -> int:
        """Hardcoded epoch ceiling at 80% of the resolved model context window."""
        window = ContextBudget.resolve_window(model, window_tokens)
        return max(1, int(window * cls.EPOCH_WINDOW_RATIO))

    @staticmethod
    def _calculate_total_tokens(msgs: List[Dict[str, Any]]) -> int:
        total = 0
        for m in msgs:
            c = m.get("content")
            if isinstance(c, str):
                total += count_tokens(c)
            elif isinstance(c, list):
                for item in c:
                    if not isinstance(item, dict):
                        continue
                    if isinstance(item.get("text"), str):
                        total += count_tokens(item["text"])
                    elif item.get("type") == "tool_use" and item.get("input") is not None:
                        total += count_tokens(json.dumps(item["input"], ensure_ascii=False))
                    elif item.get("type") == "tool_result":
                        tc = item.get("content")
                        if isinstance(tc, str):
                            total += count_tokens(tc)
                        elif tc is not None:
                            total += count_tokens(json.dumps(tc, ensure_ascii=False))
            tcalls = m.get("tool_calls")
            if tcalls:
                total += count_tokens(json.dumps(tcalls, ensure_ascii=False))
        return total

    _LANG_EXT_MAP = {
        "py": ".py", "python": ".py",
        "ts": ".ts", "typescript": ".ts",
        "js": ".js", "javascript": ".js",
        "tsx": ".tsx", "jsx": ".jsx",
        "go": ".go", "golang": ".go",
        "java": ".java",
        "rs": ".rs", "rust": ".rs",
        "c": ".c", "cpp": ".cpp", "c++": ".cpp",
        "cs": ".cs", "csharp": ".cs",
        "kt": ".kt", "kotlin": ".kt",
        "swift": ".swift", "php": ".php", "rb": ".rb", "ruby": ".rb",
        "css": ".css", "scss": ".scss",
    }

    @classmethod
    def _fold_code_blocks_in_message(cls, msg: Dict[str, Any]) -> bool:
        def _fold_text(text: str) -> str:
            def fold_match(match):
                lang = (match.group(1) or "py").lower().strip()
                ext = cls._LANG_EXT_MAP.get(lang)
                if not ext:
                    return match.group(0)
                code_text = match.group(2)
                lines = code_text.splitlines()
                if len(lines) < 25:
                    return match.group(0)
                skel = Skeletonizer.skeletonize_code(code_text, ext)
                if count_tokens(skel) < count_tokens(code_text) - 50:
                    return f"```{lang}\n// [Context Governor: AST-folded {len(lines)} lines]\n{skel}\n```"
                return match.group(0)
            return cls.CODE_BLOCK_REGEX.sub(fold_match, text)

        content = msg.get("content")
        if isinstance(content, str) and "```" in content:
            new_content = _fold_text(content)
            if new_content != content:
                msg["content"] = new_content
                return True
        elif isinstance(content, list):
            changed = False
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str) and "```" in blk["text"]:
                    new_t = _fold_text(blk["text"])
                    if new_t != blk["text"]:
                        blk["text"] = new_t
                        changed = True
            return changed
        return False

    @staticmethod
    def _starts_with_tool_result(msg: Dict[str, Any]) -> bool:
        if msg.get("role") == "tool":
            return True
        c = msg.get("content")
        if isinstance(c, list):
            return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        return False

    @classmethod
    def _consolidate_middle_turns(
        cls,
        governed_messages: List[Dict[str, Any]],
        label: str = "Context Governor Ledger",
    ) -> List[Dict[str, Any]]:
        sys_msg = (
            governed_messages[0]
            if governed_messages[0].get("role") in ("system", "developer")
            else None
        )
        start_idx = max(1 if sys_msg else 0, len(governed_messages) - 3)
        # Never cut inside a tool_use -> tool_result boundary
        while start_idx > (1 if sys_msg else 0) and cls._starts_with_tool_result(governed_messages[start_idx]):
            start_idx -= 1

        recent_turns = governed_messages[start_idx:]
        middle_turns = governed_messages[(1 if sys_msg else 0):start_idx]
        if not middle_turns:
            return governed_messages

        middle_user_intents = []
        for m in middle_turns:
            c = m.get("content", "")
            if m.get("role") == "user" and isinstance(c, str):
                first_line = c.splitlines()[0][:80]
                if first_line:
                    middle_user_intents.append(f"- {first_line}")

        summary_note = (
            f"[{label}: Consolidated {len(middle_turns)} turns]\n"
            f"Historical turn goals:\n" + "\n".join(middle_user_intents[:8]) + "\n..."
        )
        consolidated = []
        if sys_msg:
            consolidated.append(sys_msg)
        consolidated.append({"role": "system", "content": summary_note})
        consolidated.extend(recent_turns)
        return consolidated

    @classmethod
    def govern_messages(
        cls,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        target_tokens: Optional[int] = None,
        session_id: str = "governor_session",
        model: str = "",
        append_only: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Inspects message list and enforces context budgets.
        - When max_tokens is omitted (auto epoch mode) or append_only=True:
          never mutates historical turns messages[0..N-2] within an epoch;
          only folds the newly appended tail message or performs an atomic
          Epoch Rollover when exceeding 80% of the model's context window.
        - When explicit max_tokens is passed with append_only=False (default
          for explicit ceilings): runs cascading 3-stage reduction.
        """
        is_append_only = (max_tokens is None) if append_only is None else bool(append_only)
        if max_tokens is not None:
            max_budget = max_tokens
            target_budget = target_tokens if target_tokens is not None else cls.DEFAULT_TARGET_TURN_TOKENS
        else:
            max_budget = cls.resolve_epoch_limit(model)
            target_budget = target_tokens if target_tokens is not None else int(max_budget * 0.65)

        if max_budget <= 0:
            return messages, {"throttled": False, "epoch_rollover": False, "saved_tokens": 0}

        initial_tokens = cls._calculate_total_tokens(messages)
        if initial_tokens <= max_budget:
            return messages, {
                "throttled": False,
                "epoch_rollover": False,
                "initial_tokens": initial_tokens,
                "current_tokens": initial_tokens,
                "saved_tokens": 0,
                "actions": [],
                "epoch_limit": max_budget,
            }

        actions_taken = []
        governed_messages = [dict(m) for m in messages]
        current_tokens = initial_tokens
        epoch_rollover = False

        if is_append_only:
            # Tail-only fold: never mutate messages[0..N-2] so prefix KV-cache holds
            if governed_messages and cls._fold_code_blocks_in_message(governed_messages[-1]):
                current_tokens = cls._calculate_total_tokens(governed_messages)
                actions_taken.append("ast_fold_tail_code")

            # Atomic Epoch Rollover only when still above epoch limit
            if current_tokens > max_budget and len(governed_messages) > 6:
                governed_messages = cls._consolidate_middle_turns(
                    governed_messages, label="Epoch Ledger"
                )
                actions_taken.append("epoch_rollover")
                epoch_rollover = True
                current_tokens = cls._calculate_total_tokens(governed_messages)
        else:
            # Explicit tight-budget cascading reduction
            folded_any = False
            for idx in range(len(governed_messages) - 1):
                if cls._fold_code_blocks_in_message(governed_messages[idx]):
                    folded_any = True

            current_tokens = cls._calculate_total_tokens(governed_messages)
            if folded_any and current_tokens < initial_tokens:
                actions_taken.append("ast_fold_code_blocks")

            if current_tokens > target_budget:
                compacted_any = False
                for idx in range(len(governed_messages) - 1):
                    msg = governed_messages[idx]
                    if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                        raw_user = msg["content"]
                        if len(raw_user.strip()) > 15 and "[TOCP Intent]" not in raw_user:
                            comp = PromptCompactor.compact(raw_user, enable_distill=False)
                            if comp["saved_tokens"] >= 5:
                                msg["content"] = f"[TOCP Intent]: {comp['compact_text']}"
                                compacted_any = True
                current_tokens = cls._calculate_total_tokens(governed_messages)
                if compacted_any:
                    actions_taken.append("compact_user_history")

            if current_tokens > target_budget and len(governed_messages) > 6:
                governed_messages = cls._consolidate_middle_turns(
                    governed_messages, label="Context Governor Ledger"
                )
                actions_taken.append("consolidate_deep_history")
                current_tokens = cls._calculate_total_tokens(governed_messages)

        saved = max(0, initial_tokens - current_tokens)
        if saved > 0:
            try:
                tracker = MetricsTracker()
                tracker.record_event(
                    session_id=session_id,
                    event_type="EPOCH_ROLLOVER" if epoch_rollover else "CONTEXT_GOVERNOR",
                    original_tokens=initial_tokens,
                    optimized_tokens=current_tokens,
                    details={
                        "actions": actions_taken,
                        "max_budget": max_budget,
                        "target_budget": target_budget,
                        "epoch_rollover": epoch_rollover,
                    },
                    measurement="measured",
                )
            except Exception:
                pass

        return governed_messages, {
            "throttled": saved > 0 or epoch_rollover,
            "epoch_rollover": epoch_rollover,
            "initial_tokens": initial_tokens,
            "current_tokens": current_tokens,
            "saved_tokens": saved,
            "actions": actions_taken,
            "epoch_limit": max_budget,
        }


class ContextBudget:
    """Context Budget HUD: estimates how much of the model's context window the
    current session has consumed, based on TokenSafe-instrumented flows.

    Honesty (METRICS.md): only bytes that flowed through TokenSafe instruments
    are counted (tool slices, skeletons, compacted prompts, folded history).
    Un-instrumented traffic (agent reasoning, output tokens) is invisible here,
    so `used_estimated` is a FLOOR, not an exact figure — always labeled
    estimated."""

    # Conservative defaults by model family substring (tokens)
    WINDOW_DEFAULTS = [
        ("gemini", 1_000_000),
        ("claude", 200_000),
        ("gpt", 128_000),
        ("codex", 128_000),
        ("o1", 200_000),
        ("o3", 200_000),
    ]
    DEFAULT_WINDOW = 200_000

    @classmethod
    def resolve_window(cls, model: str = "", window_tokens: Optional[int] = None) -> int:
        """Explicit window > env override > model-family default."""
        if window_tokens and int(window_tokens) > 0:
            return int(window_tokens)
        env = os.environ.get("TOKENSAFE_CONTEXT_WINDOW", "")
        if env.strip().isdigit() and int(env) > 0:
            return int(env)
        model_l = (model or "").lower()
        for family, window in cls.WINDOW_DEFAULTS:
            if family in model_l:
                return window
        return cls.DEFAULT_WINDOW

    # Category mapping for the HUD breakdown (event_type -> bucket)
    CATEGORY_MAP = {
        "TOOL_READ": "tools", "GRAPH_SKELETON": "tools", "AST_SKELETON": "tools",
        "SCOPED_SEARCH": "tools", "DIR_LIST": "tools", "COMMAND_FILTER": "tools",
        "TEAM_CACHE_HIT": "tools", "SUBAGENT_DEDUP": "tools",
        "HISTORY_FOLD": "history",
        "EPOCH_ROLLOVER": "history",
        "PROMPT": "prompts",
        "CONTEXT_GOVERNOR": "other", "OUTPUT_GUARD": "other", "SECRET_GUARD": "other",
        "GRAPH_TOPOLOGY": "other",
    }

    @classmethod
    def estimate(cls, tracker: MetricsTracker, session_id: str = "mcp_session",
                 model: str = "", window_tokens: Optional[int] = None,
                 history_tokens: int = 0, transcript_path: str = "") -> Dict[str, Any]:
        """Estimate context-window usage for the session.

        history_tokens / transcript_path upgrade the HUD from a pure
        instrumented floor to a semi-real estimate: the REAL mass of past
        conversation turns (measured from the transcript by the caller via
        HistoryCompactor) is counted into the history bucket. Callers that
        only know the transcript path can pass transcript_path instead; the
        transcript is probed with HistoryCompactor.measure_transcript."""
        window = cls.resolve_window(model, window_tokens)
        breakdown = {"tools": 0, "history": 0, "prompts": 0, "other": 0}
        instrumented_events = 0
        try:
            with tracker._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT event_type, COALESCE(SUM(optimized_tokens), 0) AS received, COUNT(*) AS cnt "
                    "FROM events WHERE session_id = ? GROUP BY event_type;",
                    (session_id or "",)
                )
                for row in cursor.fetchall():
                    ev_type = row["event_type"]
                    instrumented_events += int(row["cnt"] or 0)
                    # Snapshot events (CONTEXT_GOVERNOR / EPOCH_ROLLOVER) reflect whole-context size, not additive intake.
                    if ev_type in ("CONTEXT_GOVERNOR", "EPOCH_ROLLOVER"):
                        continue
                    bucket = cls.CATEGORY_MAP.get(ev_type, "other")
                    breakdown[bucket] += int(row["received"] or 0)
        except Exception:
            pass

        # Real history mass (estimated label per METRICS.md): the transcript
        # content genuinely sits in the model context even though no TokenSafe
        # instrument metered it.
        transcript_mass = int(history_tokens or 0)
        if transcript_mass <= 0 and transcript_path:
            try:
                from src.history_compactor import HistoryCompactor
                transcript_mass = HistoryCompactor.measure_transcript(transcript_path)
            except Exception:
                transcript_mass = 0
        if transcript_mass > breakdown["history"]:
            breakdown["history"] = transcript_mass

        used = sum(breakdown.values())
        pct = round(used / window * 100.0, 1) if window > 0 else 0.0
        if pct >= 95:
            threshold, recommendation = "critical", (
                "Context nearly exhausted. Call tokensafe:compact_conversation on the "
                "session transcript now, or start a fresh session carrying the ledger.")
        elif pct >= 80:
            threshold, recommendation = "warning", (
                "Context is heavy. Prefer tokensafe:smart_read_file with symbol= for "
                "further reads and consider tokensafe:compact_conversation.")
        elif pct >= 50:
            threshold, recommendation = "notice", (
                "Half the estimated window is used. Keep reads surgical (symbol=, include_deps=false).")
        else:
            threshold, recommendation = "healthy", "Context budget healthy."

        if transcript_mass > 0:
            caveat = ("Semi-real estimate: history mass is measured from the session "
                      "transcript; agent reasoning and output tokens stay invisible to "
                      "local hooks, so this remains a lower bound.")
        else:
            caveat = ("Floor estimate: only TokenSafe-instrumented flows are counted "
                      "(agent reasoning/output is invisible to local hooks).")

        return {
            "window_tokens": window,
            "used_estimated": used,
            "used_pct": pct,
            "breakdown": breakdown,
            "instrumented_events": instrumented_events,
            "threshold": threshold,
            "recommendation": recommendation,
            "caveat": caveat,
            "measurement": "estimated",
        }
