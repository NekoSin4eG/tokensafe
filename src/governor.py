"""
TokenSafe Context Window Governor & Token Ceiling Engine.
Enforces deterministic token limits per turn or session (Budget Autothrottle).
If context exceeds threshold, applies multi-stage cascading reduction:
Stage 1: AST-folds large inert code blocks into signatures
Stage 2: Compacts intermediate user turns into TOCP Intent IR
Stage 3: Consolidates oldest middle history into State Ledger
"""

import os
import re
from typing import Dict, List, Any, Tuple, Optional
from src.tokenizer import count_tokens
from src.skeletonizer import Skeletonizer
from src.compactor import PromptCompactor
from src.metrics import MetricsTracker

class ContextGovernor:
    """Monitors and enforces context ceilings before LLM dispatch."""

    DEFAULT_MAX_TURN_TOKENS = int(os.environ.get("TOKENSAFE_MAX_TURN_TOKENS", "16000"))
    DEFAULT_TARGET_TURN_TOKENS = int(os.environ.get("TOKENSAFE_TARGET_TURN_TOKENS", "10000"))

    CODE_BLOCK_REGEX = re.compile(r"```([a-zA-Z0-9_\-+]*)\n(.*?)```", re.DOTALL)

    @classmethod
    def govern_messages(
        cls,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        target_tokens: Optional[int] = None,
        session_id: str = "governor_session"
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Inspects message list. If total tokens exceed max_tokens, applies cascading
        reductions until within target_tokens budget.
        """
        max_budget = max_tokens if max_tokens is not None else cls.DEFAULT_MAX_TURN_TOKENS
        target_budget = target_tokens if target_tokens is not None else cls.DEFAULT_TARGET_TURN_TOKENS

        if max_budget <= 0:
            return messages, {"throttled": False, "saved_tokens": 0}

        def calculate_total_tokens(msgs: List[Dict[str, Any]]) -> int:
            total = 0
            for m in msgs:
                c = m.get("content")
                if isinstance(c, str):
                    total += count_tokens(c)
                elif isinstance(c, list):
                    for item in c:
                        if isinstance(item, dict) and "text" in item:
                            total += count_tokens(item["text"])
            return total

        initial_tokens = calculate_total_tokens(messages)
        if initial_tokens <= max_budget:
            return messages, {
                "throttled": False,
                "initial_tokens": initial_tokens,
                "current_tokens": initial_tokens,
                "saved_tokens": 0,
                "actions": []
            }

        actions_taken = []
        governed_messages = [dict(m) for m in messages]
        current_tokens = initial_tokens

        # --- Stage 1: AST Skeletonize large inert code blocks in previous turns ---
        for idx in range(len(governed_messages) - 1): # leave last user turn untouched
            msg = governed_messages[idx]
            content = msg.get("content")
            if not isinstance(content, str) or "```" not in content:
                continue

            def fold_match(match):
                lang = (match.group(1) or "py").lower()
                code_text = match.group(2)
                lines = code_text.splitlines()
                if len(lines) < 25:
                    return match.group(0)

                ext = f".{lang}" if not lang.startswith(".") else lang
                if ext not in (".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".java", ".rs", ".c", ".cpp"):
                    ext = ".py"

                skel = Skeletonizer.skeletonize_code(code_text, ext)
                if count_tokens(skel) < count_tokens(code_text) - 50:
                    return f"```{lang}\n// [Context Governor: AST-folded {len(lines)} lines]\n{skel}\n```"
                return match.group(0)

            new_content = cls.CODE_BLOCK_REGEX.sub(fold_match, content)
            if new_content != content:
                governed_messages[idx]["content"] = new_content

        current_tokens = calculate_total_tokens(governed_messages)
        if current_tokens < initial_tokens:
            actions_taken.append("ast_fold_code_blocks")

        # --- Stage 2: S-Expression Compaction of older user prompts ---
        if current_tokens > target_budget:
            for idx in range(len(governed_messages) - 1):
                msg = governed_messages[idx]
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    raw_user = msg["content"]
                    if len(raw_user.strip()) > 15 and "[TOCP Intent]" not in raw_user:
                        comp = PromptCompactor.compact(raw_user, enable_deepseek=False)
                        if comp["saved_tokens"] >= 5:
                            msg["content"] = f"[TOCP Intent]: {comp['compact_text']}"
            current_tokens = calculate_total_tokens(governed_messages)
            if "ast_fold_code_blocks" not in actions_taken or current_tokens < initial_tokens:
                actions_taken.append("compact_user_history")

        # --- Stage 3: Sliding ledger summarization of deep history if still over target ---
        if current_tokens > target_budget and len(governed_messages) > 6:
            # Keep first system message and last 3 turns intact, fold middle
            sys_msg = governed_messages[0] if governed_messages[0].get("role") == "system" else None
            recent_turns = governed_messages[-3:]
            middle_turns = governed_messages[1:-3] if sys_msg else governed_messages[:-3]

            middle_user_intents = []
            for m in middle_turns:
                c = m.get("content", "")
                if m.get("role") == "user" and isinstance(c, str):
                    first_line = c.splitlines()[0][:80]
                    middle_user_intents.append(f"- {first_line}")

            summary_note = (
                f"[Context Governor Ledger: Consolidated {len(middle_turns)} turns]\n"
                f"Historical turn goals:\n" + "\n".join(middle_user_intents[:5]) + "\n..."
            )
            consolidated = []
            if sys_msg:
                consolidated.append(sys_msg)
            consolidated.append({"role": "system", "content": summary_note})
            consolidated.extend(recent_turns)
            governed_messages = consolidated
            actions_taken.append("consolidate_deep_history")
            current_tokens = calculate_total_tokens(governed_messages)

        saved = max(0, initial_tokens - current_tokens)
        if saved > 0:
            try:
                tracker = MetricsTracker()
                tracker.record_event(
                    session_id=session_id,
                    event_type="CONTEXT_GOVERNOR",
                    original_tokens=initial_tokens,
                    optimized_tokens=current_tokens,
                    details={
                        "actions": actions_taken,
                        "max_budget": max_budget,
                        "target_budget": target_budget
                    }
                )
            except Exception:
                pass

        return governed_messages, {
            "throttled": True,
            "initial_tokens": initial_tokens,
            "current_tokens": current_tokens,
            "saved_tokens": saved,
            "actions": actions_taken
        }
