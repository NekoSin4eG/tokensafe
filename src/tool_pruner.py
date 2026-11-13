"""
TokenSafe Session-Stable Tool Pruner (proxy-stage, v3.0.0).

The proxy sees the FULL tools[] array of every MCP server. Most sessions use a
small fraction of the declared tools, yet every unused schema burns floor
tokens on every request (~8.7K in a typical session).

Opt-in (TOKENSAFE_TOOL_PRUNING=1). Instead of Anthropic-style tool search
(impossible here: the proxy never EXECUTES tools — the agent does), we pick a
relevance-ranked SUBSET once per pseudo-session and keep it byte-stable for
the whole session, so the tools[] KV-cache prefix never churns mid-session:

- Pseudo-session key = sha256(first_user_content + model)[:16] — the first
  user message is replayed verbatim in every request of a session, so the key
  is stable; a new session almost always means a new key.
- Subset = top-K tools scored against the first user message (keyword overlap
  over name+description) + the always-allow allowlist.
- The agent still knows ALL its tools, so a pruned-tool call still executes —
  we just measure the miss rate (TOOL_PRUNE_MISS) to watch quality impact.

State: data/proxy_tool_subsets.json {key: {tools: [names], ts}} with TTL
pruning. Stateless for requests of an already-keyed session.
"""

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.tokenizer import count_tokens

STATE_FILENAME = "proxy_tool_subsets.json"
STATE_TTL_SECONDS = 24 * 3600
STATE_MAX_KEYS = 64
DEFAULT_KEEP = 14
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_\-]{2,}")

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your",
    "you", "are", "can", "will", "not", "use", "using", "when", "what",
    "which", "how", "all", "any", "get", "set", "list", "name", "path",
    "file", "type", "string", "integer", "boolean", "object", "array",
    "optional", "required", "default", "returns", "return", "description",
    "parameter", "parameters", "schema", "tool", "tools", "please", "need",
    "want", "make", "add", "fix", "code", "project",
}


def _tool_name(tool: Dict[str, Any]) -> str:
    if isinstance(tool, dict):
        if tool.get("name"):
            return str(tool["name"])
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
    return ""


def _tool_desc(tool: Dict[str, Any]) -> str:
    if isinstance(tool, dict):
        if isinstance(tool.get("description"), str):
            return tool["description"]
        fn = tool.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("description"), str):
            return fn["description"]
    return ""


def _session_key(messages: List[Dict[str, Any]], model: str) -> str:
    first_user = ""
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            first_user = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break
    return hashlib.sha256((first_user + "|" + str(model or "")).encode("utf-8", "replace")).hexdigest()[:16]


def _allowlist() -> List[str]:
    raw = os.environ.get("TOKENSAFE_TOOL_PRUNE_ALLOWLIST", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _state_path() -> str:
    from src.paths import resolve_data_dir
    return os.path.join(resolve_data_dir(), STATE_FILENAME)


def _load_state() -> Dict[str, Any]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _prune_state(state: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    alive = {k: v for k, v in state.items()
             if isinstance(v, dict) and (now - float(v.get("ts", 0) or 0)) < STATE_TTL_SECONDS}
    if len(alive) > STATE_MAX_KEYS:
        ranked = sorted(alive.items(), key=lambda kv: -float(kv[1].get("ts", 0) or 0))
        alive = dict(ranked[:STATE_MAX_KEYS])
    return alive


def _score_tool(tool: Dict[str, Any], query_tokens: set) -> float:
    name = _tool_name(tool)
    text = f"{name} {_tool_desc(tool)}".lower()
    tokens = set(_TOKEN_RE.findall(text)) - _STOPWORDS
    overlap = len(tokens & query_tokens)
    # Name hits outweigh description hits; exact name-in-query is strongest.
    name_bonus = 3.0 if name.lower() in query_tokens else 0.0
    return overlap + name_bonus


def select_subset(
    tools: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    model: str = "",
) -> Tuple[Optional[List[str]], Dict[str, Any]]:
    """
    Returns (subset_tool_names_or_None, meta). None means "no pruning"
    (flag off, pool small enough, or no user content to score against).
    The result is deterministic per (session_key, pool); selection happens
    once per pseudo-session and is then served from the state file.
    """
    meta: Dict[str, Any] = {"applied": False, "pool": len(tools or [])}
    if not tools or not isinstance(tools, list):
        return None, meta
    if os.environ.get("TOKENSAFE_TOOL_PRUNING", "").strip().lower() not in ("1", "true", "yes"):
        return None, meta

    keep = DEFAULT_KEEP
    raw_keep = os.environ.get("TOKENSAFE_TOOL_PRUNE_KEEP", "")
    if raw_keep.strip().isdigit():
        keep = max(1, int(raw_keep.strip()))
    if len(tools) <= keep:
        meta["reason"] = "pool<=keep"
        return None, meta

    key = _session_key(messages, model)
    if not key:
        return None, meta
    state = _prune_state(_load_state())
    entry = state.get(key)
    if isinstance(entry, dict) and isinstance(entry.get("tools"), list):
        cached_names = [str(n) for n in entry["tools"]]
        meta.update({"applied": True, "session_key": key, "cached": True, "subset": len(cached_names)})
        return cached_names, meta

    # Score once per pseudo-session against the first user message.
    query_text = ""
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            query_text = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break
    query_tokens = set(_TOKEN_RE.findall(query_text.lower())) - _STOPWORDS

    allow = set(_allowlist())
    scored = sorted(
        tools,
        key=lambda t: (-_score_tool(t, query_tokens), _tool_name(t)),
    )
    subset: List[str] = []
    for t in scored:
        name = _tool_name(t)
        if not name or name in subset:
            continue
        if len(subset) >= keep and name not in allow:
            continue
        subset.append(name)
    # Allowlisted tools always make the cut, even beyond K.
    for t in tools:
        name = _tool_name(t)
        if name in allow and name not in subset:
            subset.append(name)

    meta.update({"applied": True, "session_key": key, "cached": False, "subset": len(subset), "pool": len(tools)})
    state[key] = {"tools": subset, "ts": time.time()}
    _save_state(state)
    return subset, meta


def apply_subset(
    tools: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    model: str = "",
    record_metrics: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Filters tools[] down to the session subset. Returns (tools, meta)."""
    subset_names, meta = select_subset(tools, messages, model)
    if not subset_names:
        return tools, meta
    keep_set = set(subset_names)
    pruned = [t for t in tools if _tool_name(t) in keep_set]
    if len(pruned) == len(tools):
        meta["applied"] = False
        return tools, meta
    meta["saved_tokens"] = max(0, count_tokens(json.dumps(tools, ensure_ascii=False)) - count_tokens(json.dumps(pruned, ensure_ascii=False)))
    if record_metrics and meta.get("saved_tokens"):
        try:
            from src.metrics import MetricsTracker
            MetricsTracker().record_event(
                session_id=f"prune_{meta.get('session_key', 'sess')}",
                event_type="TOOL_PRUNE",
                original_tokens=count_tokens(json.dumps(tools, ensure_ascii=False)),
                optimized_tokens=count_tokens(json.dumps(pruned, ensure_ascii=False)),
                details={"pool": len(tools), "subset": len(pruned), "cached": meta.get("cached", False)},
                measurement="measured",
            )
        except Exception:
            pass
    return pruned, meta


def detect_prune_miss(response_json: Dict[str, Any], sent_tool_names: set) -> Optional[str]:
    """Returns the first tool name the model called that was NOT in the sent
    subset (quality-impact telemetry; the call still flows to the agent)."""
    if not isinstance(response_json, dict):
        return None
    for choice in response_json.get("choices") or []:
        msg = choice.get("message") if isinstance(choice, dict) else None
        for tc in (msg or {}).get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or "")
            if name and name not in sent_tool_names:
                return name
    return None
