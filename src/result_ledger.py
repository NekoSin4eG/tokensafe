"""
TokenSafe Tool Result Ledger (proxy-stage, v3.0.0).

After an LLM has CONSUMED a tool result, that result keeps burning input tokens
on every subsequent request — in long sessions old tool results are 50-70% of
the messages mass. The Ledger replaces aged, oversized tool results with a
compact summary + a deterministic re-hydration anchor, and archives the
original to disk so nothing is lost.

Design invariants:
- Stateless across requests: a folded result is recognized by its marker, so
  the fold is idempotent and survives proxy restarts without session state.
- Cache-aware: an anchor is byte-deterministic (no timestamps/counters), so a
  folded prefix stays KV-cache stable; each result is folded exactly once.
- Quality-safe: only results older than the recent tail and above a token
  floor are folded; originals are archived locally; anchors point back to the
  exact re-run / re-read command.

Applies to the OpenAI chat shape (role:"tool" messages paired with
assistant.tool_calls) — the only shape the proxy ingests on
/v1/chat/completions. Runs BEFORE ContextGovernor so the governor sees the
already-lightened mass.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from src.tokenizer import count_tokens

# Deterministic marker used both for idempotence detection and as the model-
# visible anchor header. No timestamps, no hit counters (KV-cache stability).
FOLD_MARKER = "[TokenSafe Ledger:"
ARCHIVE_DIRNAME = "tool_ledger"
MAX_ARCHIVE_CHARS = 256 * 1024  # refuse to archive oversized artifacts
HEAD_LINES = 12
TAIL_LINES = 12

# Tool classification reused from the history compactor (same vocabulary the
# whole product uses for read/edit/command tools).
from src.history_compactor import _COMMAND_TOOLS, _READ_TOOLS  # noqa: E402
from src.history_compactor import _arg_value, _COMMAND_KEYS, _PATH_KEYS  # noqa: E402

_LANG_EXT_MAP = {
    "py": ".py", "python": ".py", "ts": ".ts", "tsx": ".tsx", "js": ".js",
    "jsx": ".jsx", "go": ".go", "java": ".java", "rs": ".rs", "c": ".c",
    "cpp": ".cpp", "cs": ".cs", "kt": ".kt", "swift": ".swift", "php": ".php",
    "rb": ".rb", "css": ".css", "scss": ".scss",
}


def _env_flag(name: str, default: str = "1") -> bool:
    raw = os.environ.get(name, default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _archive_result(text: str, sha: str) -> Optional[str]:
    """Archives the original result under data/tool_ledger/<sha>.json.
    Returns the archive path, or None when oversized/refused."""
    if len(text) > MAX_ARCHIVE_CHARS:
        return None
    try:
        from src.paths import resolve_data_dir
        archive_dir = os.path.join(resolve_data_dir(), ARCHIVE_DIRNAME)
        os.makedirs(archive_dir, exist_ok=True)
        path = os.path.join(archive_dir, f"{sha[:16]}.json")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"sha256": sha, "chars": len(text), "content": text}, f, ensure_ascii=False)
        return path
    except Exception:
        return None


def _head_tail(text: str, head: int = HEAD_LINES, tail: int = TAIL_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= head + tail + 2:
        return text
    return "\n".join(lines[:head] + [f"... [{len(lines) - head - tail} lines omitted] ..."] + lines[-tail:])


def _compact_read_result(text: str, path: str) -> Optional[str]:
    """AST skeleton for source reads; head/tail fallback for unknown types.
    Produces both candidates when possible and returns the shortest — the
    skeleton only wins when it actually compresses substantially."""
    ext = os.path.splitext(path or "")[1].lower() or None
    candidates: List[str] = []
    lines = text.splitlines()
    if ext and ext in list(_LANG_EXT_MAP.values()) and len(lines) >= 25:
        try:
            from src.skeletonizer import Skeletonizer
            skel = Skeletonizer.skeletonize_code(text, ext)
            if skel and count_tokens(skel) < count_tokens(text) * 0.6:
                candidates.append(
                    f"[folded AST skeleton of {len(lines)} lines]\n"
                    + skel
                    + f"\n[... {len(lines)} lines total; full source archived]"
                )
        except Exception:
            pass
    folded = _head_tail(text)
    if folded != text:
        candidates.append(folded)
    if not candidates:
        return None
    return min(candidates, key=count_tokens)


def _compact_command_result(text: str) -> Optional[str]:
    """Test summaries first (PASS: ...), log-style head/tail otherwise."""
    try:
        from src.command_sanitizer import CommandSanitizer
        compacted = CommandSanitizer.compact_test_output(text, 0)
        if compacted is not text and len(compacted) < len(text) * 0.6:
            return compacted + "\n[... full output archived]"
    except Exception:
        pass
    folded = _head_tail(text)
    return folded if folded != text else None


def _compact_generic_result(text: str) -> Optional[str]:
    folded = _head_tail(text)
    return folded if folded != text else None


def _maybe_distill_history(text: str) -> Optional[str]:
    """Optional managed-cloud summarization (D): paid license + explicit opt-in
    only; degrades silently to the mechanical fold on any failure."""
    if not _env_flag("TOKENSAFE_HISTORY_DISTILL", "0"):
        return None
    try:
        from src.distiller import UniversalDistiller
        tier, _lic = None, None
        try:
            from src.license_manager import LicenseManager
            tier, _lic = LicenseManager.get_active_license()
        except Exception:
            return None
        if tier == "free":
            return None
        return UniversalDistiller.distill_history(text)
    except Exception:
        return None


def _compact_result(tool_name: str, tool_args: Dict[str, Any], text: str) -> Optional[str]:
    """Returns the compact replacement content, or None when nothing shorter
    was produced (caller then leaves the result untouched)."""
    if tool_name in _COMMAND_TOOLS:
        # Command results: the mechanical PASS/head-tail fold is strictly
        # better than prose distillation (deterministic, zero latency).
        return _compact_command_result(text)
    if tool_name in _READ_TOOLS:
        path = _arg_value(tool_args, _PATH_KEYS)
        return _compact_read_result(text, path)
    # Mixed/prose results: managed distillation when enabled, else head/tail.
    distilled = _maybe_distill_history(text)
    if distilled:
        return distilled
    return _compact_generic_result(text)


def _args_preview(tool_args: Dict[str, Any], tool_name: str) -> str:
    preview = _arg_value(tool_args, _PATH_KEYS) or _arg_value(tool_args, _COMMAND_KEYS)
    if not preview:
        try:
            preview = json.dumps(tool_args, ensure_ascii=False)
        except Exception:
            preview = ""
    preview = (preview or "").replace("\n", " ")
    return preview[:60] + ("..." if len(preview) > 60 else "")


def _fold_anchor(tool_name: str, preview: str, orig_tokens: int, new_tokens: int, sha: str, archive_path: Optional[str]) -> str:
    archive_note = f" Original archived at {archive_path}." if archive_path else " Original NOT archived (oversized)."
    return (
        f"{FOLD_MARKER} {tool_name} ({preview}) result consumed earlier — folded "
        f"~{orig_tokens}→{new_tokens} tokens (sha256:{sha[:8]}).{archive_note} "
        f"To re-inspect: re-run {tool_name}, or use smart_read_file(force_full=true) for file reads.]"
    )


def _result_text(content: Any) -> str:
    """Extracts the text mass of an OpenAI tool message content (str or blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
        return "\n".join(parts)
    return ""


def _set_result_text(msg: Dict[str, Any], new_text: str) -> None:
    content = msg.get("content")
    if isinstance(content, list):
        # Keep block structure; replace the first text block, drop the rest.
        replaced = False
        kept = []
        for b in content:
            if not replaced and isinstance(b, dict) and isinstance(b.get("text"), str):
                kept.append(dict(b, text=new_text))
                replaced = True
            elif isinstance(b, dict) and isinstance(b.get("text"), str):
                continue  # redundant text blocks collapse into the fold
            else:
                kept.append(b)
        if not replaced:
            kept.insert(0, {"type": "text", "text": new_text})
        msg["content"] = kept
    else:
        msg["content"] = new_text


def _assistant_tool_calls(msg: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Yields (call_id, tool_name, args) for OpenAI-style assistant tool_calls."""
    calls = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        call_id = str(tc.get("id") or "")
        name = str(fn.get("name") or "")
        raw_args = fn.get("arguments")
        args: Dict[str, Any] = {}
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    args = parsed
            except Exception:
                args = {}
        if call_id and name:
            calls.append((call_id, name, args))
    return calls


def fold_tool_results(
    messages: List[Dict[str, Any]],
    session_id: str = "proxy_session",
    keep_recent: int = 6,
    min_tokens: int = 800,
    record_metrics: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Folds aged, oversized tool results into compact anchors (in-place on a
    shallow copy of the message list). Returns (messages, stats).

    Gates: TOKENSAFE_TOOL_LEDGER=0 disables; results inside the last
    `keep_recent` messages are never folded; results under `min_tokens` stay
    verbatim; already-folded results (marker present) are never re-folded.
    """
    stats: Dict[str, Any] = {"folded": 0, "orig_tokens": 0, "new_tokens": 0, "saved_tokens": 0}
    if not messages or not _env_flag("TOKENSAFE_TOOL_LEDGER"):
        return messages, stats

    # Map call_id -> (tool_name, args) from ALL assistant turns.
    call_meta: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for call_id, name, args in _assistant_tool_calls(m):
                call_meta[call_id] = (name, args)

    protected_from = max(0, len(messages) - keep_recent)
    for idx, msg in enumerate(messages):
        if idx >= protected_from or not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        call_id = str(msg.get("tool_call_id") or "")
        tool_name, tool_args = call_meta.get(call_id, ("unknown_tool", {}))
        text = _result_text(msg.get("content"))
        if not text or FOLD_MARKER in text:
            continue  # empty or already folded — idempotence marker
        orig_tokens = count_tokens(text)
        if orig_tokens < min_tokens:
            continue
        compacted = _compact_result(tool_name, tool_args, text)
        if not compacted or count_tokens(compacted) >= orig_tokens * 0.85:
            continue  # compaction must earn its cache-break
        new_tokens = count_tokens(compacted)
        sha = _content_hash(text)
        anchor = _fold_anchor(tool_name, _args_preview(tool_args, tool_name), orig_tokens, new_tokens, sha, _archive_result(text, sha))
        final_text = f"{anchor}\n\n{compacted}" if not compacted.startswith(FOLD_MARKER) else compacted
        _set_result_text(msg, final_text)
        stats["folded"] += 1
        stats["orig_tokens"] += orig_tokens
        stats["new_tokens"] += new_tokens
        stats["saved_tokens"] += max(0, orig_tokens - new_tokens)

    if stats["folded"] and record_metrics:
        try:
            from src.metrics import MetricsTracker
            MetricsTracker().record_event(
                session_id=session_id,
                event_type="TOOL_LEDGER",
                original_tokens=stats["orig_tokens"],
                optimized_tokens=stats["new_tokens"],
                details={"folded": stats["folded"]},
                measurement="measured",
            )
        except Exception:
            pass

    return messages, stats
