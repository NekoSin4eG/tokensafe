"""Heuristic JSON repair for malformed tool-call arguments.

Small/local models (Haiku, Llama, Qwen) intermittently emit broken JSON in
tool-call argument strings: a missing closing bracket, trailing commas,
Python literals, raw newlines inside strings, or single quotes. Instead of
failing the tool call and burning a full retry roundtrip, the MCP server and
the proxy run the payload through this best-effort repairer first.

Standard library only. Every stage is tried strictly: a repaired candidate is
accepted only when `json.loads` really accepts it — no fabricated output.
"""

import json
import re
from typing import Any, List, Optional


def parse_tool_arguments(raw: Any) -> Optional[dict]:
    """Parses tool-call arguments. Dicts pass through; strings go through strict
    json.loads first, then heuristic repair. Returns None when unrepairable."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        val = json.loads(s)
        return val if isinstance(val, dict) else None
    except Exception:
        pass
    repaired = repair_json(s)
    if repaired is None:
        return None
    try:
        val = json.loads(repaired)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def repair_json(s: str) -> Optional[str]:
    """Best-effort repair of a broken JSON string. Returns repaired text that
    strict json.loads accepts, or None when nothing sensible can be produced."""
    s = s.strip().lstrip("\ufeff")
    if not s:
        return None

    py_fixed = re.sub(r"\bTrue\b", "true", s)
    py_fixed = re.sub(r"\bFalse\b", "false", py_fixed)
    py_fixed = re.sub(r"\bNone\b", "null", py_fixed)

    ctrl_fixed = _escape_control_chars_in_strings(py_fixed)

    def _strip_trailing_commas(text: str) -> str:
        return re.sub(r",(\s*[}\]])", r"\1", text)

    candidates: List[Optional[str]] = [py_fixed, ctrl_fixed, _strip_trailing_commas(ctrl_fixed)]

    # Balance missing closers, then strip trailing commas AGAIN — appending a
    # closer can leave a dangling comma right before it (e.g. '{"a": 1,').
    balanced0 = _balance_brackets(ctrl_fixed)
    for base in (balanced0, _balance_brackets(_strip_trailing_commas(ctrl_fixed))):
        if base is not None:
            candidates.append(base)
            candidates.append(_strip_trailing_commas(base))

    # Single-quote conversion only as a late resort (apostrophe risk)
    for base in list(candidates):
        sq = _single_to_double_quotes(base) if base else None
        if sq:
            candidates.append(sq)
            sb = _balance_brackets(sq)
            if sb:
                candidates.append(sb)

    # Truncation fallback (last resort): cut back to the last safe structural
    # boundary, then re-balance. Handles payloads cut MID-TOKEN that no closer
    # appender can fix — '{"a": tru', '{"count": 12' cut after a value,
    # '{"a": 1, "b": ' cut after a colon, '{"na' cut mid-key. Strictly
    # validated like every other stage: json.loads must accept the result.
    trunc = _truncate_to_last_boundary(ctrl_fixed)
    if trunc is not None:
        bases = [trunc]
        balanced = _balance_brackets(_strip_trailing_commas(trunc))
        if balanced:
            bases.insert(0, balanced)
        for base in bases:
            candidates.append(base)
            candidates.append(_strip_trailing_commas(base))

    for cand in candidates:
        if cand is None:
            continue
        try:
            json.loads(cand)
            return cand
        except Exception:
            continue
    return None


def _escape_control_chars_in_strings(s: str) -> str:
    """Escapes raw newlines/tabs/CRs that appear inside string literals."""
    out: List[str] = []
    in_str = False
    quote = ""
    escaped = False
    for ch in s:
        if in_str:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == quote:
                in_str = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        else:
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            out.append(ch)
    return "".join(out)


def _truncate_to_last_boundary(s: str) -> Optional[str]:
    """Cuts the text back to the last safe structural boundary outside strings —
    right after an opener ('{', '['), separator (','), colon, closing bracket,
    or closing quote. This is the repair stage for truncated payloads cut
    mid-token, where appending closers alone cannot produce valid JSON.
    Returns None when there is no safe boundary to cut to."""
    last = -1
    in_str = False
    quote = ""
    escaped = False
    for i, ch in enumerate(s):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_str = False
                last = i  # right after a closing quote is a safe boundary
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch in "{[,:}]":
            last = i
    if last < 0:
        return None
    cut = s[: last + 1].rstrip()
    if cut.endswith(":"):
        cut += " null"  # a dangling colon needs a value before the closers
    return cut


def _balance_brackets(s: str) -> Optional[str]:
    """Appends missing closing brackets/braces (string-aware). Returns None when
    the structure cannot be balanced by appending closers alone."""
    if not s:
        return None
    stack: List[str] = []
    in_str = False
    quote = ""
    escaped = False
    for ch in s:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None
            stack.pop()
    if in_str:
        # Unterminated string: close it before the pending brackets
        return s + '"' + "".join(reversed(stack))
    return s + "".join(reversed(stack))


def _single_to_double_quotes(s: str) -> Optional[str]:
    """Converts single-quoted strings to double quotes. Only safe when the text
    contains no double quotes at all (so an unambiguous dialect is broken)."""
    if '"' in s or "'" not in s:
        return None
    return s.replace("'", '"')
