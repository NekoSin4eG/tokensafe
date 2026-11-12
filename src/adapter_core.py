"""
Shared core for cross-agent lifecycle-hook adapters (Claude Code, Cursor, ZCode).

Design rules (see METRICS.md):
- Savings are only recorded for REAL reductions. Blocked reads/commands never
  reach the model, but their "would-have-been" mass is a heuristic guess, so
  PROMPT injections and GUARD_*/COMMAND_FILTER/LOOP_GUARD events are labeled
  estimated; events whose original/optimized tokens come from real bytes or
  metered I/O stay measured.
- Adapters ALWAYS fail open: an adapter crash must never block the host agent.
  (Cursor additionally blocks on invalid JSON, so adapters return valid
  allow-JSON from the exception handler.)
"""

import os
import sys
import re
import json
from typing import Dict, Any, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# One-shot session directives, injected once per session by every agent's
# SessionStart surface (Antigravity hooks/session_start.py as a persistent
# systemMessage, Claude Code as hookSpecificOutput.additionalContext).
TOKENSAFE_DIRECTIVES = (
    "[TokenSafe Mandatory Directives]:\n"
    "• Terminal code searches (grep/rg/find on source files) are BLOCKED by TokenSafe Guard.\n"
    "• To search keywords, symbols, or configs: call 'tokensafe:scoped_search(query, scope)'.\n"
    "• To inspect component or function definitions: call 'tokensafe:smart_read_file(path, symbol)' "
    "(1-hop deps default to compact signatures via dep_mode='signature'; pass dep_mode='full' only when "
    "dependency bodies are needed, or force_full=true if a cache pointer references content missing from context).\n"
    "• To locate dependencies & God Nodes: call 'tokensafe:get_architecture_graph(project_path)'.\n"
    "• To inspect all exported types/interfaces: call 'tokensafe:get_api_surface(project_path)'.\n"
    "• Before large multi-step tasks: call 'tokensafe:context_budget(model=...)' to check the context window budget.\n"
    "• When context_budget reports warning/critical: call 'tokensafe:compact_conversation(transcript_path=...)' to fold history before continuing.\n"
    "• Prefer 'edit_file' over 'write_file' for existing files."
)

# High-precision deny patterns: secrets and irreversible destruction only.
_SECRET_PATTERNS = [
    r"sk_live_[0-9a-zA-Z]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[0-9a-zA-Z]{30,}",
    r"xox[baprs]-[0-9a-zA-Z-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
_DESTRUCTIVE_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*\s+)*-?[rf]{2,}[a-zA-Z]*\s+(--\s+)?/(?:\s|$)",   # rm -rf /
    r"rm\s+(-[a-zA-Z]*\s+)*-?[rf]{2,}[a-zA-Z]*\s+~(?:/\s*)?(?:\s|$)",  # rm -rf ~
    r"dd\s+.*of=/dev/[sd]",
    r"mkfs\.\w+",
    r"chmod\s+-R\s+777\s+/(?:\s|$)",
    r":\(\)\{.*\};:",                                                  # fork bomb
]
_TLS_BYPASS_PATTERNS = [
    r"curl\s+(-[a-zA-Z]+\s+)*-k\b",
    r"curl\s+.*--insecure",
    r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*0",
    r"wget\s+.*--no-check-certificate",
    r"git\s+-c\s+[a-z.]*sslVerify\s*=\s*false",
    r"GIT_SSL_NO_VERIFY\s*=\s*(?:true|1)",
]
# Commands that never terminate on their own — an agent burning a turn on them
# stalls until timeout. Intentional runs can still proceed via the "ask" path.
_INFINITE_PATTERNS = [
    r"\btail\b[^|;&]*\s-f\b",
    r"\bwatch\s+",
    r"\bdocker\s+logs\b[^|;&]*\s(?:-f\b|--follow)",
    r"\bkubectl\s+logs\b[^|;&]*\s-f\b",
    r"\bdocker\s+attach\b",
    # vitest runs an interactive watcher unless the `run` subcommand is given
    r"\bvitest\b(?![^|;&]*\brun\b)",
    r"\b(?:jest|vitest)\b[^|;&]*--watch\b",
]

# Lockfiles/junk that must never be dumped into an agent context.
_JUNK_READ_PATTERNS = [
    r"(^|/)(bun\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.lock|go\.sum|composer\.lock)$",
    r"\.(min\.js|min\.css|bundle\.js|map)$",
    r"(^|/)dist/",
    r"(^|/)node_modules/",
]


def _get_tracker():
    try:
        from src.metrics import MetricsTracker
        return MetricsTracker()
    except Exception:
        return None


def compact_for_injection(prompt: str, agent: str, session_id: str) -> Tuple[str, Dict[str, Any]]:
    """Compacts a user prompt into its TOCP form for context injection.

    Returns (context_text, meta). context_text is '' when compaction is not
    worthwhile. Savings are recorded as estimated: the compressed form is
    injected alongside the original (no verified replace in host agents)."""
    meta: Dict[str, Any] = {"agent": agent, "compacted": False}
    if not prompt or len(prompt.strip()) < 5:
        return "", meta
    try:
        from src.compactor import PromptCompactor
        from src.tokenizer import count_tokens
        compaction = PromptCompactor.compact(prompt, enable_distill=False)
        saved = compaction.get("saved_tokens", 0)
        if saved < 5:
            return "", meta
        tracker = _get_tracker()
        if tracker:
            try:
                tracker.record_event(
                    session_id=session_id or f"hook_{agent}",
                    event_type="PROMPT",
                    original_tokens=compaction["original_tokens"],
                    optimized_tokens=compaction["compact_tokens"],
                    details={"agent": agent, "mode": "hook_inject", "tier": compaction.get("tier", "REGEX")},
                )
            except Exception:
                pass
        meta.update({"compacted": True, "saved_tokens": saved, "reduction_pct": compaction.get("reduction_pct", 0)})
        return f"[TOCP Compressed Intent]: {compaction['compact_text']}", meta
    except Exception:
        return "", meta


def guard_command(command: str, agent: str, session_id: str = "", cwd: str = "") -> Dict[str, Any]:
    """Pre-execution shell guard. Returns:
    {decision: allow|deny|ask, reason, saved_tokens} where saved_tokens is the
    MEASURED mass of blocked output (0 for allowed commands)."""
    result = {"decision": "allow", "reason": "", "saved_tokens": 0}
    if not command or not command.strip():
        return result
    cmd = command.strip()
    text = cmd

    def blocked(kind: str, reason: str, decision: str = "deny"):
        est = max(50, len(cmd) // 4)  # blocked mass never reaches the model
        tracker = _get_tracker()
        if tracker:
            try:
                tracker.record_event(
                    session_id=session_id or f"hook_{agent}",
                    event_type=f"GUARD_{kind}",
                    original_tokens=est,
                    optimized_tokens=0,
                    details={"agent": agent, "command": cmd[:120]},
                )
            except Exception:
                pass
        result.update({"decision": decision, "reason": reason, "saved_tokens": est})

    for pat in _SECRET_PATTERNS:
        if re.search(pat, text):
            blocked("SECRET", f"Blocked: command contains a hardcoded secret ({pat[:12]}... pattern)")
            return result
    for pat in _DESTRUCTIVE_PATTERNS:
        if re.search(pat, text):
            blocked("DESTRUCTIVE", "Blocked: irreversible/destructive operation")
            return result
    # Full TokenSafeGuard delegation: the Antigravity path (pre_tool_use.py) runs
    # TokenSafeGuard.inspect_command, and the Claude Code / Cursor adapters must
    # honor the SAME policy suite (GIT_SECRET_STAGING, SQL_INJECTION,
    # INSECURE_CORS, DATA_EXFILTRATION, DESTRUCTIVE_RM flag/target variants, and
    # raw code search with the MCP-tools guidance) instead of a regex subset.
    # Fail-open on import problems, like every adapter guard path.
    try:
        from src.guard import TokenSafeGuard
        guard_res = TokenSafeGuard.inspect_command(cmd, cwd=cwd or None)
    except Exception:
        guard_res = None
    if guard_res is not None and getattr(guard_res, "blocked", False):
        blocked(guard_res.check_name or "GUARD", guard_res.reason)
        return result
    for pat in _TLS_BYPASS_PATTERNS:
        if re.search(pat, text):
            blocked("TLS_VERIFY_BYPASS", "Certificate verification disabled — confirm this is intentional", decision="ask")
            return result
    for pat in _INFINITE_PATTERNS:
        if re.search(pat, text):
            blocked("INFINITE", "Command never terminates on its own (follow/streaming) — run it in the background or confirm intent", decision="ask")
            return result
    return result


def guard_read(file_path: str, agent: str, session_id: str = "") -> Dict[str, Any]:
    """Pre-read guard: denies lockfiles/bundles/dist junk that would burn the
    context window. Blocked reads are recorded as measured savings."""
    result = {"decision": "allow", "reason": "", "saved_tokens": 0}
    if not file_path:
        return result
    normalized = file_path.replace("\\", "/")
    for pat in _JUNK_READ_PATTERNS:
        if re.search(pat, normalized):
            est = 4000  # typical lockfile dump size floor
            tracker = _get_tracker()
            if tracker:
                try:
                    tracker.record_event(
                        session_id=session_id or f"hook_{agent}",
                        event_type="GUARD_JUNK_READ",
                        original_tokens=est,
                        optimized_tokens=0,
                        details={"agent": agent, "file": os.path.basename(normalized)},
                    )
                except Exception:
                    pass
            result.update({
                "decision": "deny",
                "reason": f"TokenSafe: '{os.path.basename(normalized)}' is machine-generated bulk "
                          f"(~{est // 1000}k tokens). Inspect it via 'tokensafe:scoped_search' or grep a symbol instead.",
                "saved_tokens": est,
            })
            return result
    return result
