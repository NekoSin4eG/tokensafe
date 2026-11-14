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


def _py_literals_outside_strings(s: str) -> str:
    """Replaces Python literals (True/False/None) with JSON equivalents ONLY
    outside string literals; string content is copied verbatim."""
    out: List[str] = []
    buf: List[str] = []
    in_str = False
    quote = ""
    escaped = False

    def _flush() -> None:
        if buf:
            seg = "".join(buf)
            buf.clear()
            seg = re.sub(r"\bTrue\b", "true", seg)
            seg = re.sub(r"\bFalse\b", "false", seg)
            seg = re.sub(r"\bNone\b", "null", seg)
            out.append(seg)

    for ch in s:
        if in_str:
            out.append(ch)  # string content is data — copied verbatim
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            _flush()
            in_str = True
            quote = ch
            out.append(ch)
            continue
        buf.append(ch)
    _flush()
    return "".join(out)


def repair_json(s: str) -> Optional[str]:
    """Best-effort repair of a broken JSON string. Returns repaired text that
    strict json.loads accepts, or None when nothing sensible can be produced."""
    s = s.strip().lstrip("\ufeff")
    if not s:
        return None

    # Python-literal replacement is STRING-AWARE: blind re.sub rewrote
    # 'None'/'True' INSIDE string values — a music-index fixture keyed by
    # artist names turned "Sixpence None the Richer" into "Sixpence null the
    # Richer" and fabricated keys (QA sweep). Only literals OUTSIDE strings
    # are Python dialect.
    py_fixed = _py_literals_outside_strings(s)

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

    for cand in candidates:
        if cand is None:
            continue
        try:
            json.loads(cand)
            return cand
        except Exception:
            continue

    # Truncation fallback (TRUE last resort — every closer-appending and
    # quote-conversion strategy above has failed): retreat boundary-by-
    # boundary. Handles cuts that land MID-TOKEN and right after a COMPLETED
    # KEY ('{"a":1,"后"' — a dangling key with no colon), where appending
    # closers alone can never produce valid JSON. Strictly validated like
    # every other stage: json.loads must accept the result.
    for cut in _iter_truncation_candidates(ctrl_fixed):
        stripped = _strip_trailing_commas(cut)
        trimmed = cut.rstrip(", ")  # a dangling comma at the cut point (strip
        # only removes commas BEFORE a closer, and the cut point has none yet)
        for cand in (cut, stripped, _balance_brackets(cut), _balance_brackets(stripped),
                     trimmed, _balance_brackets(trimmed)):
            if not cand:
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


def _iter_truncation_candidates(s: str, max_boundaries: int = 10):
    """Yields progressively earlier truncation cuts at safe structural
    boundaries outside strings — after an opener ('{', '['), separator (','),
    colon, closing bracket, or closing quote. A dangling colon gains a 'null'.

    ONLY runs when the text is genuinely TRUNCATED — an unclosed bracket or
    string at EOF. A balanced-but-broken payload ('{"a": ]}') is not a
    truncation and must stay unrepairable (repairing it would mean deleting
    user content). The LAST boundary alone is often unrepairable (a cut right
    after a COMPLETED KEY leaves a dangling key with no colon —
    '{"a":1,"后"'), so the caller retreats boundary-by-boundary; strict
    json.loads validation accepts the first parseable candidate. Real-world
    case: a 544KB search_index.json cut mid-key (QA sweep)."""
    boundaries: List[int] = []
    stack: List[str] = []
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
                boundaries.append(i)
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch in "{[":
            stack.append(ch)
            boundaries.append(i)
        elif ch in "}]":
            if stack:
                stack.pop()
            boundaries.append(i)
        elif ch in ",:":
            boundaries.append(i)
    if not boundaries or (not in_str and not stack):
        return  # balanced and closed — nothing was truncated
    attempts = 0
    for pos in reversed(boundaries):
        if attempts >= max_boundaries:
            break
        attempts += 1
        cut = s[: pos + 1].rstrip()
        if cut.endswith(":"):
            cut += " null"  # a dangling colon needs a value before the closers
        yield cut


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
