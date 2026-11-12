"""
Universal Model Context Protocol (MCP) Server for Token Optimizer.
Provides MCP stdio JSON-RPC 2.0 interface compatible with Claude Desktop, Cursor, Windsurf, Cline, and Zed.
Exposes tools for smart file reading, dependency prefetching, prompt compression, and command sanitizing.
"""

import sys
import os
import stat
import time
import json
import re
import threading
import traceback
import fnmatch
from typing import List, Tuple, Optional

# Ensure project root in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.compactor import PromptCompactor
from src.skeletonizer import Skeletonizer
from src.slicer import ToolSlicer
from src.dependency_prefetcher import DependencyPrefetcher
from src.command_sanitizer import CommandSanitizer
from src.loop_detector import LoopDetector
from src.metrics import MetricsTracker
from src.dashboard import Dashboard
from src.graph_engine import GraphEngine
from src.tokenizer import count_tokens
from src.history_compactor import HistoryCompactor
from src.team_cache import TeamCache
from src.governor import ContextBudget
from src.agent_firewall import AgentFirewall
from src.diff_slicer import DiffSlicer
from src.guard import TokenSafeGuard
from src.scoped_search import ScopedSearch
from src.license_manager import LicenseManager

SERVER_INFO = {
    "name": "tokensafe-mcp",
    "version": "2.5.1"
}

# MCP client attribution: the initialize handshake carries clientInfo, which
# lets every session-scoped metric name the REAL agent instead of a shared
# anonymous bucket (see METRICS.md by_agent).
_MCP_CLIENT_NAME = ""
_TOOLS_LISTED = False
_EPOCH_GEN = 0
_IN_PROCESS_DELIVERED = {}
_KNOWN_POINTER_CLIENTS = (
    "antigravity", "claude", "cursor", "windsurf", "cline", "roo",
    "codex", "zcode", "copilot", "gemini", "zed", "vscode",
)


def _set_mcp_client(name: str):
    global _MCP_CLIENT_NAME
    clean = re.sub(r"[^a-z0-9_-]", "", str(name or "").lower())[:30]
    _MCP_CLIENT_NAME = clean


def _mcp_session_id() -> str:
    return f"mcp:{_MCP_CLIENT_NAME}" if _MCP_CLIENT_NAME else "mcp_session"


def _client_supports_pointers() -> bool:
    """Out-of-the-box capability detection (zero env vars): returns True once the
    MCP client has fetched tools/list (seeing force_full) or identified as an
    interactive AI coding host."""
    if _TOOLS_LISTED:
        return True
    return bool(_MCP_CLIENT_NAME and any(k in _MCP_CLIENT_NAME for k in _KNOWN_POINTER_CLIENTS))


TOOLS = [
    {
        "name": "smart_read_file",
        "description": "Reads a code file using AST skeletonization and graph-guided slicing to save 90-99% context tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the code file"},
                "symbol": {"type": "string", "description": "Optional: targeted function or class name for graph-guided skeletonization"},
                "include_deps": {"type": "boolean", "description": "Optional: whether to include 1-hop dependencies (default: true). Set false for 0-hop surgical isolation."},
                "dep_mode": {"type": "string", "enum": ["signature", "full"], "description": "Optional: how 1-hop dependencies are returned when include_deps is true. 'signature' (default, -95%+ tokens) returns compact 1-line signatures; 'full' inlines full dependency bodies."},
                "force_full": {"type": "boolean", "description": "Optional: bypass session pointer deduplication and return the full skeleton directly (use only if your context was compacted or you are a fresh subagent lacking earlier reads)."},
                "start_line": {"type": "integer", "description": "Optional: starting line number"},
                "end_line": {"type": "integer", "description": "Optional: ending line number"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "get_architecture_graph",
        "description": "Returns top architectural God Nodes and dependency topology for the target project. Call this BEFORE reading files or searching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Absolute root path of the project directory. Highly recommended to pass explicitly."}
            }
        }
    },
    {
        "name": "get_api_surface",
        "description": "Generates and returns a compact markdown map of all exported interfaces, classes, and types in the project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Absolute root path of the project directory. Highly recommended to pass explicitly."}
            }
        }
    },
    {
        "name": "compress_prompt",
        "description": "Compresses a verbose developer prompt into an ultra-dense TOCP S-Expression via DeepSeek-V3 / Regex.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Natural language request from developer"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "context_budget",
        "description": "Context Budget HUD: estimates how much of the model's context window this session has consumed (tools/history/prompts breakdown, 50/80/95% thresholds). Call before large multi-step tasks. When the threshold is warning or critical, immediately call tokensafe:compact_conversation on the session transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Optional: model name to resolve window size (default: env TOKENSAFE_CONTEXT_WINDOW or 200k)"},
                "window_tokens": {"type": "integer", "description": "Optional: explicit context window size in tokens"},
                "session_id": {"type": "string", "description": "Optional: session identifier (default: mcp_session)"},
                "transcript_path": {"type": "string", "description": "Optional: path to the session JSONL transcript; when provided, REAL history mass is measured instead of the instrumented floor (recommended)"}
            }
        }
    },
    {
        "name": "compact_conversation",
        "description": "On-demand context compaction: folds a long agent conversation transcript into a compact Consolidated State Ledger. Use when tokensafe:context_budget reports warning/critical, when the context window feels heavy, or before starting a new subtask inside the same session. The transcript path comes from your host session (hook payload transcript_path / session start context).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript_path": {"type": "string", "description": "Path to the JSONL transcript of the current session"},
                "invocation_num": {"type": "integer", "description": "Optional: current turn number (default 3; folding requires >= 3 turns)"}
            },
            "required": ["transcript_path"]
        }
    },
    {
        "name": "sanitize_command",
        "description": "Per-stage shell command rewrites: prunes lockfiles, caps unbounded git log/show/blame/rg "
                     "and docker/kubectl logs, quiets npm/pip installs, bounds cat/curl output. Compound commands "
                     "(a && b ; c) are optimized per stage without breaking operators. For unit-test runs prefer "
                     "the `tests` CLI wrapper (tokensafe tests [pytest]) over raw `python3 -m unittest discover`. "
                     "Pass cwd for test-runner commands: the guard reads <cwd>/package.json to force jest/vitest "
                     "into run-once mode (CI=true, --watch=false, --forceExit) so agents never hang on watch mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Raw bash command line"},
                "cwd": {"type": "string", "description": "Optional working directory the command will run in; enables the package.json peek that identifies the runner behind opaque yarn/npm test scripts"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "slice_diff",
        "description": "Slices unified diffs, omitting lockfiles and minimizing context lines to reduce tokens by up to 80%.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "Raw git diff text"},
                "max_context": {"type": "integer", "description": "Maximum surrounding context lines (default: 2)"}
            },
            "required": ["diff"]
        }
    },
    {
        "name": "get_token_savings_metrics",
        "description": "Returns cumulative token and cost savings analytics.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "guard_inspect",
        "description": "Pre-flight security & hygiene audit for commands or code snippets. Detects secret leaks, SQL injection, TLS bypass (incl. git-level), CORS wildcard, destructive commands, raw grep, and never-terminating streams (tail -f, watch, docker attach).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Optional: Terminal command line to audit"},
                "code": {"type": "string", "description": "Optional: Code snippet, replacement diff, or file content to audit"},
                "file_path": {"type": "string", "description": "Optional: File path being written or modified"}
            }
        }
    },
    {
        "name": "list_dir",
        "description": "Returns a clean, token-efficient directory tree up to depth N without permissions, timestamps, or ignored folders (node_modules, .git, dist, .env). Replaces verbose ls -la.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the directory (relative to project root or absolute)"},
                "depth": {"type": "integer", "description": "Maximum traversal depth (default: 2, max: 4)"},
                "max_entries": {"type": "integer", "description": "Maximum entries to display before pruning (default: 80)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "scoped_search",
        "description": "Safe, token-bounded symbol and text search across the codebase. Replaces raw grep/find with clean, line-numbered markdown matches (-90% tokens).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text, identifier, or symbol name to search for"
                },
                "scope": {
                    "type": "string",
                    "enum": ["code", "config", "all"],
                    "description": "Scope of search: 'code' (source files only), 'config' (json, toml, yaml), or 'all' (default: 'all')"
                },
                "path": {
                    "type": "string",
                    "description": "Directory or project path to search (alias for project_path)"
                },
                "project_path": {
                    "type": "string",
                    "description": "Optional root directory path (defaults to current project)"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matching lines to return (default: 25, max: 100)"
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether search is case-sensitive (default: false)"
                },
                "use_regex": {
                    "type": "boolean",
                    "description": "Whether query is a regular expression (default: false)"
                }
            },
            "required": ["query"]
        }
    }
]

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def build_dir_tree(dir_path: str, max_depth: int = 2, max_entries: int = 80) -> str:
    """
    Builds a clean, compact markdown tree for dir_path.
    Ignores heavy folders (node_modules, dist, .git, etc.) and redacts sensitive files (.env*).
    """
    IGNORE_DIRS = {
        'node_modules', '.git', 'dist', 'build', '.next', 'coverage',
        '__pycache__', '.turbo', '.vite', '.output', 'data',
        '.venv', 'venv', '.cache', '.idea', '.vscode',
        'Library', 'Applications', 'Movies', 'Music', 'Pictures'
    }
    IGNORE_FILES = {
        '*.lock', '*.lockb', '*.map', '.DS_Store', 'Thumbs.db', 'package-lock.json',
        'pnpm-lock.yaml', 'yarn.lock', 'Cargo.lock', 'composer.lock'
    }
    SECRET_FILES = {
        '.env', '.env.local', '.env.production', '.env.staging', '.env.development'
    }

    if not os.path.exists(dir_path):
        return f"Error: Directory not found: {dir_path}"
    if not os.path.isdir(dir_path):
        return f"Error: Path is not a directory: {dir_path}"

    base_name = os.path.basename(os.path.abspath(dir_path)) or dir_path
    lines = [f"📁 **{base_name}/** (max depth: {max_depth})"]
    entries_count = 0
    start_time = time.monotonic()

    def _traverse(curr_dir: str, prefix: str, current_depth: int):
        nonlocal entries_count
        if current_depth > max_depth or entries_count >= max_entries:
            return
        if time.monotonic() - start_time > 3.0:
            return

        try:
            items = sorted(os.listdir(curr_dir))
        except Exception as e:
            lines.append(f"{prefix}└── [Permission Denied: {e}]")
            return

        dirs = []
        files = []
        for it in items:
            if it.startswith('.') and it not in SECRET_FILES and it != '.gitignore':
                continue
            full_p = os.path.join(curr_dir, it)
            if os.path.isdir(full_p):
                if it in IGNORE_DIRS:
                    continue
                dirs.append(it)
            else:
                files.append(it)

        all_items = [(d, True) for d in dirs] + [(f, False) for f in files]
        total_items = len(all_items)

        for i, (name, is_dir) in enumerate(all_items):
            if entries_count >= max_entries:
                lines.append(f"{prefix}└── ... ({total_items - i} more items omitted for token efficiency)")
                break

            is_last = (i == total_items - 1)
            pointer = "└── " if is_last else "├── "
            extension_prefix = "    " if is_last else "│   "
            item_path = os.path.join(curr_dir, name)

            if is_dir:
                entries_count += 1
                lines.append(f"{prefix}{pointer}📁 {name}/")
                _traverse(item_path, prefix + extension_prefix, current_depth + 1)
            else:
                if name in SECRET_FILES:
                    lines.append(f"{prefix}{pointer}🔒 {name} [PROTECTED SECRET]")
                    entries_count += 1
                    continue
                if any(fnmatch.fnmatch(name, pat) for pat in IGNORE_FILES):
                    continue
                try:
                    sz = os.path.getsize(item_path)
                    sz_str = format_file_size(sz)
                except Exception:
                    sz_str = ""
                entries_count += 1
                size_label = f" ({sz_str})" if sz_str else ""
                lines.append(f"{prefix}{pointer}{name}{size_label}")

    _traverse(dir_path, "", 1)
    lines.append("\n*Pruned noisy directories (node_modules, dist, .git) & lockfiles to conserve tokens.*")
    return "\n".join(lines)

def _resolve_project_root(args: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Safely resolves the target project root without falling back to system root dirs.
    """
    target = args.get("project_path") or args.get("root_dir") or args.get("path") or args.get("directory")
    system_roots = {"/", "/Users", "/home", "/root", os.path.expanduser("~")}

    if target:
        target_abs = os.path.abspath(target)
        if os.path.isfile(target_abs):
            target_abs = os.path.dirname(target_abs)
        if target_abs not in system_roots and os.path.isdir(target_abs):
            return target_abs, None

    # Check environment variables
    for env_var in ("AGY_PROJECT_PATH", "WORKSPACE_FOLDER", "PROJECT_DIR", "PWD"):
        candidate = os.environ.get(env_var)
        if candidate:
            cand_abs = os.path.abspath(candidate)
            if cand_abs not in system_roots and os.path.isdir(cand_abs):
                return cand_abs, None

    # Check cwd
    cwd = os.getcwd()
    if cwd:
        cwd_abs = os.path.abspath(cwd)
        if cwd_abs not in system_roots and os.path.isdir(cwd_abs):
            return cwd_abs, None

    # Hint with active projects if found under ~/Documents/projects
    projects_dir = os.path.expanduser("~/Documents/projects")
    if os.path.isdir(projects_dir):
        try:
            subdirs = [d for d in os.listdir(projects_dir) if not d.startswith(".") and os.path.isdir(os.path.join(projects_dir, d))]
            if subdirs:
                examples = ", ".join([f"'{d}'" for d in subdirs[:4]])
                return None, f"Error: Please specify 'project_path' explicitly. Available projects in ~/Documents/projects: {examples}. Example: project_path='{os.path.join(projects_dir, subdirs[0])}'"
        except Exception:
            pass

    return None, "Error: Invalid or root project_path. Please specify an explicit 'project_path' pointing to your project directory (e.g. project_path='/path/to/project')."

MCP_TOOL_TIMEOUT = float(os.environ.get("TOKENSAFE_MCP_TIMEOUT", "8.0"))


def _fallback_error(tool_name: str, reason: str) -> dict:
    """Returns a non-blocking, actionable error telling the AI model to try another approach."""
    clean_reason = (reason or "unknown error").strip()
    return {
        "isError": True,
        "content": [{
            "type": "text",
            "text": (
                f"⚠️ TokenSafe MCP tool '{tool_name}' could not complete ({clean_reason}). "
                "Try an alternative approach instead of repeating this exact call: "
                "narrow the target 'path'/'project_path' or 'depth', inspect a specific file with 'smart_read_file', "
                "or fall back to standard 'read_file' / bounded 'run_command'."
            )
        }]
    }


class _NoOpTracker:
    """Fallback metrics tracker if SQLite DB is temporarily unavailable or locked."""
    db_path = ""
    def record_event(self, *args, **kwargs):
        return 0
    def get_summary(self, *args, **kwargs):
        return {}
    def get_last_event(self, *args, **kwargs):
        return None


class _SafeTrackerProxy:
    """Wraps MetricsTracker so transient SQLite errors never interrupt MCP tool output."""
    def __init__(self, inner):
        self._inner = inner
        self.db_path = getattr(inner, "db_path", "")

    def record_event(self, *args, **kwargs):
        try:
            return self._inner.record_event(*args, **kwargs)
        except Exception:
            return 0

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _get_safe_tracker():
    try:
        return _SafeTrackerProxy(MetricsTracker())
    except Exception:
        return _NoOpTracker()


def _team_cache_key(path: str, symbol: str, dep_mode: str = "signature") -> str:
    """Builds a portable, content-hash-validated key shared across machines."""
    abs_path = os.path.abspath(path)
    repo_root = GraphEngine.find_project_root(abs_path)
    try:
        rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")
    except ValueError:
        rel_path = os.path.basename(abs_path)
    content_hash = AgentFirewall._file_content_hash(abs_path)[:16]
    return f"{rel_path}::{symbol or ''}::{dep_mode}::{content_hash}"


def _team_skeleton_lookup(path: str, symbol: str, dep_mode: str = "signature") -> Optional[str]:
    """Team cache pre-warm lookup: returns a teammate's cached skeleton or None.

    Never blocks the caller for more than ~250ms: the network fetch runs in a
    daemon thread with a short join. Non-team installs skip the network entirely."""
    try:
        is_team, _ = TeamCache._is_team_tier()
        if not is_team:
            return None
        cache_key = _team_cache_key(path, symbol, dep_mode=dep_mode)
        box: dict = {}

        def _fetch():
            try:
                box["value"] = TeamCache.get("SKELETON", cache_key)
            except Exception:
                pass

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(0.25)
        return box.get("value")
    except Exception:
        return None


def _team_skeleton_prewarm(
    path: str,
    symbol: str,
    skeleton_text: str,
    tokens_saved: int,
    dep_mode: str = "signature",
):
    """Publishes a freshly built skeleton to the team cache (async, team tier only).

    Teammate B's later smart_read_file on the same symbol hits the cache instead
    of re-reading and re-skeletonizing the file."""
    try:
        cache_key = _team_cache_key(path, symbol, dep_mode=dep_mode)
        TeamCache.put("SKELETON", cache_key, skeleton_text, tokens_saved=tokens_saved)
    except Exception:
        pass


def _resolve_dep_mode(args: dict) -> Tuple[bool, str]:
    inc_deps = bool(args.get("include_deps", True))
    if not inc_deps:
        return False, "none"
    raw_mode = str(args.get("dep_mode") or "").strip().lower()
    if raw_mode in ("signature", "full"):
        return True, raw_mode
    return True, "signature"


def handle_tool_call(name: str, args: dict) -> dict:
    if args is None or not isinstance(args, dict):
        args = {}
    try:
        result = _dispatch_tool_call(name, args)
        if name == "smart_read_file" and isinstance(result, dict) and not result.get("isError"):
            content_parts = result.get("content") or []
            text_out = "".join(p.get("text", "") for p in content_parts if isinstance(p, dict))
            path = args.get("path", "")
            symbol = (args.get("symbol") or "").strip()
            _, eff_dep_mode = _resolve_dep_mode(args)
            cache_sym = f"{symbol}@{eff_dep_mode}" if (symbol and eff_dep_mode != "signature") else symbol
            if text_out and path:
                abs_path = os.path.abspath(path)
                cur_hash = AgentFirewall._file_content_hash(abs_path)
                if cur_hash and args.get("start_line") is None and args.get("end_line") is None:
                    _IN_PROCESS_DELIVERED[(abs_path, symbol, eff_dep_mode)] = (cur_hash, _EPOCH_GEN)
                # Never overwrite cached skeleton with a firewall header or pointer hit
                if not text_out.startswith("// [TokenSafe Subagent Firewall:"):
                    try:
                        AgentFirewall.record_cache(
                            session_id=_mcp_session_id(),
                            file_path=path,
                            content=text_out,
                            symbol=cache_sym,
                            tracker=_get_safe_tracker()
                        )
                    except Exception:
                        pass
        return result
    except Exception as e:
        return _fallback_error(name, f"{type(e).__name__}: {str(e)}")


def _quota_banner_if_due(quota: dict, tracker, session_id: str) -> str:
    """Emits a concise Community 500k quota CTA on turn #1 of exceedance and every 5 turns."""
    if not quota.get("is_quota_exceeded"):
        return ""
    if LicenseManager.should_emit_quota_banner(tracker, session_id=session_id, every_n_turns=5):
        return f"// {quota.get('warning', '')}\n"
    return ""


def _dispatch_tool_call(name: str, args: dict) -> dict:
    global _EPOCH_GEN
    tracker = _get_safe_tracker()
    quota = LicenseManager.check_quota(tracker)
    is_quota_exceeded = bool(quota.get("is_quota_exceeded", False))

    if name == "smart_read_file":
        path = args.get("path", "")
        symbol = args.get("symbol", "")
        inc_deps, eff_dep_mode = _resolve_dep_mode(args)
        force_full = bool(args.get("force_full", False))
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        # Clamp explicit line ranges to sane bounds so bad indices can never produce
        # empty output or negative-index slicing artifacts.
        try:
            if start_line is not None:
                start_line = max(1, int(start_line))
            if end_line is not None:
                end_line = int(end_line)
        except (TypeError, ValueError):
            return _fallback_error("smart_read_file", "start_line/end_line must be integers")

        if not path or not os.path.exists(path):
            return {"isError": True, "content": [{"type": "text", "text": f"File not found: {path}"}]}
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                return _fallback_error("smart_read_file", f"path is not a regular file: {path}")
        except OSError as e:
            return _fallback_error("smart_read_file", f"cannot stat path: {e}")

        # Community 500k quota reached: soft-degrade to bounded raw read (disable Graph/AST/Pointer/Team Cache)
        if is_quota_exceeded:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if start_line is not None and end_line is not None:
                e_line = min(end_line, len(lines))
                if start_line > e_line:
                    return _fallback_error("smart_read_file", f"start_line {start_line} is beyond end of file ({len(lines)} lines)")
                raw_chunk = "".join(lines[start_line - 1 : e_line])
            else:
                cap = min(250, len(lines))
                raw_chunk = "".join(lines[:cap])
                if len(lines) > cap:
                    raw_chunk += f"\n// [TokenSafe Community 500k limit reached: showing raw lines 1-{cap} of {len(lines)}. AST/graph slicing & pointer dedup are paused.]\n"
            banner = _quota_banner_if_due(quota, tracker, _mcp_session_id())
            return {"content": [{"type": "text", "text": banner + raw_chunk}]}

        abs_path = os.path.abspath(path)
        sym_clean = (symbol or "").strip()
        cache_sym = f"{sym_clean}@{eff_dep_mode}" if (sym_clean and eff_dep_mode != "signature") else sym_clean

        if start_line is None and end_line is None:
            cur_hash = AgentFirewall._file_content_hash(abs_path)
            delivered = _IN_PROCESS_DELIVERED.get((abs_path, sym_clean, eff_dep_mode))
            already_here = bool(delivered and cur_hash and delivered == (cur_hash, _EPOCH_GEN))
            allow_pointer = (not force_full) and _client_supports_pointers() and already_here
            is_hit, cached_text, saved_dedup = AgentFirewall.check_cache(
                _mcp_session_id(),
                path,
                symbol=cache_sym,
                tracker=tracker,
                allow_pointer=allow_pointer,
            )
            if is_hit and cached_text:
                return {"content": [{"type": "text", "text": cached_text}]}

        # Check loop detector (skip when force_full explicitly recovers from a pointer)
        if not force_full:
            is_loop, repeats, msg = LoopDetector.check_loop(_mcp_session_id(), "smart_read_file", args)
            if is_loop:
                return {"isError": True, "content": [{"type": "text", "text": f"⚠️ {msg}"}]}

        # Only inline extra DependencyPrefetcher stubs when full deps or whole-file mode requested
        prefetch_block = ""
        if not sym_clean or eff_dep_mode == "full":
            prefetch_block, _ = DependencyPrefetcher.prefetch_for_file(path)

        # Check targeted symbol or skeleton
        if start_line is not None and end_line is not None:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            end_line = min(end_line, len(lines))
            if start_line > end_line:
                return _fallback_error("smart_read_file", f"start_line {start_line} is beyond end of file ({len(lines)} lines)")
            content = "".join(lines[start_line - 1 : end_line])
            return {"content": [{"type": "text", "text": (prefetch_block or "") + content}]}

        if symbol:
            # Team pre-warm hit: a teammate already skeletonized this symbol
            team_skel = _team_skeleton_lookup(path, symbol, dep_mode=eff_dep_mode)
            if team_skel:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    raw_code = f.read()
                orig_t = count_tokens(raw_code)
                skel_t = count_tokens(team_skel)
                tracker.record_event(
                    session_id=_mcp_session_id(),
                    event_type="TEAM_CACHE_HIT",
                    original_tokens=orig_t,
                    optimized_tokens=skel_t,
                    details={"file": os.path.basename(path), "symbol": symbol, "dep_mode": eff_dep_mode, "source": "team_cache"}
                )
                header = f"// [TokenSafe Team-Cached Skeleton for '{symbol}' | shared by a teammate]\n"
                return {"content": [{"type": "text", "text": (prefetch_block or "") + header + team_skel}]}

            try:
                graph, rel_path = GraphEngine.for_file(path)
                graph_skel = graph.graph_guided_skeletonize(
                    path,
                    target_symbol=symbol,
                    include_deps=inc_deps,
                    dep_mode=eff_dep_mode,
                    collapse_imports=(eff_dep_mode != "full"),
                )
                if graph_skel:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        raw_code = f.read()
                    orig_t = count_tokens(raw_code)
                    skel_t = count_tokens(graph_skel)
                    saved = max(0, orig_t - skel_t)
                    tracker.record_event(
                        session_id=_mcp_session_id(),
                        event_type="GRAPH_SKELETON",
                        original_tokens=orig_t,
                        optimized_tokens=skel_t,
                        details={
                            "file": os.path.basename(path),
                            "symbol": symbol,
                            "saved_tokens": saved,
                            "include_deps": inc_deps,
                            "dep_mode": eff_dep_mode,
                        }
                    )
                    if not inc_deps:
                        dep_label = "Surgical single-symbol isolation"
                    elif eff_dep_mode == "full":
                        dep_label = "Full body + 1-hop dependencies"
                    else:
                        dep_label = "Full body + 1-hop signature deps"
                    header = f"// [TokenSafe Graph-Guided Skeleton for '{symbol}' | {dep_label}]\n"
                    _team_skeleton_prewarm(path, symbol, graph_skel, saved, dep_mode=eff_dep_mode)
                    return {"content": [{"type": "text", "text": (prefetch_block or "") + header + graph_skel}]}
            except Exception:
                pass

            overwrite, metrics = ToolSlicer.optimize_read_file({"Path": path}, context_hint=symbol)
            if overwrite:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                s_line = overwrite["StartLine"]
                e_line = overwrite["EndLine"]
                content = "".join(lines[s_line - 1 : e_line])
                header = f"// [TOCP Targeted Window: lines {s_line}-{e_line} for '{symbol}']\n"
                return {"content": [{"type": "text", "text": (prefetch_block or "") + header + content}]}

        # Check AST skeleton
        skel_path, orig_t, skel_t = Skeletonizer.get_skeleton_file(path)
        if skel_path and os.path.exists(skel_path):
            with open(skel_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"content": [{"type": "text", "text": content}]}

        # Default read
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"content": [{"type": "text", "text": (prefetch_block or "") + content}]}

    elif name == "compress_prompt":
        prompt = args.get("prompt", "")
        if is_quota_exceeded:
            emit_warn = LicenseManager.should_emit_quota_banner(tracker, session_id=_mcp_session_id(), every_n_turns=5)
            payload = {
                "original_text": prompt,
                "compact_text": prompt,
                "saved_tokens": 0,
                "tier": "QUOTA_EXCEEDED",
                "quota_exceeded": True,
                "warning": quota.get("warning") if emit_warn else "Community 500k monthly limit reached; prompt compaction paused.",
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}
        res = PromptCompactor.compact(prompt, enable_deepseek=True)
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}]}

    elif name == "context_budget":
        model = str(args.get("model") or "")
        window_tokens = args.get("window_tokens")
        session_id = str(args.get("session_id") or _mcp_session_id())
        transcript_path = str(args.get("transcript_path") or "")
        try:
            if window_tokens is not None:
                window_tokens = int(window_tokens)
        except (TypeError, ValueError):
            window_tokens = None
        budget = ContextBudget.estimate(
            tracker, session_id=session_id, model=model, window_tokens=window_tokens,
            transcript_path=transcript_path
        )
        return {"content": [{"type": "text", "text": json.dumps(budget, indent=2, ensure_ascii=False)}]}

    elif name == "compact_conversation":
        transcript_path = args.get("transcript_path", "")
        try:
            invocation_num = max(3, int(args.get("invocation_num", 3) or 3))
        except (TypeError, ValueError):
            invocation_num = 3
        if not transcript_path or not os.path.exists(transcript_path):
            return {"isError": True, "content": [{"type": "text", "text": f"Transcript not found: {transcript_path}"}]}
        if is_quota_exceeded:
            LicenseManager.should_emit_quota_banner(tracker, session_id=_mcp_session_id(), every_n_turns=5)
            return {"content": [{"type": "text", "text": json.dumps({
                "compactable": False,
                "quota_exceeded": True,
                "monthly_saved": quota.get("monthly_saved"),
                "monthly_limit": quota.get("monthly_limit"),
                "reason": quota.get("warning"),
            }, indent=2, ensure_ascii=False)}]}
        ledger, orig_t, folded_t, stats = HistoryCompactor.fold_transcript(transcript_path, invocation_num)
        if not ledger:
            return {"content": [{"type": "text", "text": json.dumps({
                "compactable": False,
                "reason": "Transcript too short to fold (needs >= 4 steps and >= 1000 history tokens)"
            }, indent=2)}]}
        _EPOCH_GEN += 1
        saved = max(0, orig_t - folded_t)
        # Incremental metrics guard (same policy as the PreInvocation hook):
        # repeated compaction of the SAME transcript must not re-record the
        # full history mass — count only the delta since the last fold.
        prev_event = tracker.get_last_event(_mcp_session_id(), "HISTORY_FOLD")
        prev_same_path = bool(
            prev_event and isinstance(prev_event.get("details"), dict)
            and prev_event["details"].get("transcript_path") == transcript_path
        )
        prev_orig = prev_event["details"].get("total_accum_orig", 0) if prev_same_path else 0
        delta_orig = max(0, orig_t - prev_orig)
        if not prev_same_path or delta_orig >= 1500:
            inc_orig = orig_t if not prev_same_path else delta_orig
            tracker.record_event(
                session_id=_mcp_session_id(),
                event_type="HISTORY_FOLD",
                original_tokens=inc_orig,
                optimized_tokens=folded_t,
                details={"mode": "manual_compact_conversation", "turns": invocation_num,
                         "transcript_path": transcript_path, "total_accum_orig": orig_t, **stats}
            )
        return {"content": [{"type": "text", "text": json.dumps({
            "compactable": True,
            "ledger": ledger,
            "original_tokens": orig_t,
            "folded_tokens": folded_t,
            "saved_tokens": saved,
            "note": "Inject this ledger as a system message and start fresh context from it; savings are labeled estimated (digest injected, history not removed).",
            "stats": stats
        }, indent=2, ensure_ascii=False)}]}

    elif name == "sanitize_command":
        cmd = args.get("command", "")
        # Step 1: TokenSafe Guard security inspection
        guard_res = TokenSafeGuard.inspect_command(cmd)
        if guard_res.blocked:
            tracker.record_event(
                session_id=_mcp_session_id(),
                event_type=f"GUARD_{guard_res.check_name}",
                original_tokens=count_tokens(cmd),
                optimized_tokens=0,
                details={"cmd": cmd[:100], "reason": guard_res.reason}
            )
            result = {
                "original_command": cmd,
                "sanitized_command": None,
                "blocked": True,
                "check_name": guard_res.check_name,
                "reason": guard_res.reason,
                "recommendation": "Blocked by TokenSafe Guard. Do not execute this command."
            }
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

        # Step 2: Command sanitization (paused at Community 500k quota while Security Guard stays active)
        if is_quota_exceeded:
            emit_note = LicenseManager.should_emit_quota_banner(tracker, session_id=_mcp_session_id(), every_n_turns=5)
            result = {
                "original_command": cmd,
                "sanitized_command": cmd,
                "blocked": False,
                "tokens_saved": 0,
                "quota_exceeded": True,
                "reason": quota.get("warning") if emit_note else "Community 500k monthly limit reached; command output compression paused (Security Guard active).",
            }
            if guard_res.warning_only:
                result["warning"] = guard_res.reason
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd, cwd=args.get("cwd") or None)
        result = {
            "original_command": cmd,
            "sanitized_command": new_cmd or cmd,
            "blocked": False,
            "tokens_saved": saved,
            "reason": reason or "No modification needed"
        }
        if guard_res.warning_only:
            result["warning"] = guard_res.reason
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    elif name == "guard_inspect":
        cmd = args.get("command")
        code = args.get("code")
        file_path = args.get("file_path", "")

        results = []
        if cmd is not None:
            c_res = TokenSafeGuard.inspect_command(cmd)
            results.append({
                "target": "command",
                "input": cmd,
                "allowed": not c_res.blocked,
                "blocked": c_res.blocked,
                "check_name": c_res.check_name,
                "reason": c_res.reason,
                "warning_only": c_res.warning_only
            })
            if c_res.blocked:
                tracker.record_event(
                    session_id=_mcp_session_id(),
                    event_type=f"GUARD_{c_res.check_name}",
                    original_tokens=count_tokens(cmd),
                    optimized_tokens=0,
                    details={"cmd": cmd[:100], "reason": c_res.reason}
                )
            else:
                # Adapter-level command checks (v2.1.0): TokenSafeGuard's
                # command audit does not scan for literal credentials, so
                # deny-level secrets/destruction come from the adapter core —
                # the same patterns the Claude Code / Cursor shell guards use.
                try:
                    from src.adapter_core import (_SECRET_PATTERNS, _DESTRUCTIVE_PATTERNS,
                                                  _INFINITE_PATTERNS, _TLS_BYPASS_PATTERNS)
                    hit = None
                    for pat in _SECRET_PATTERNS:
                        if re.search(pat, cmd):
                            hit = ("SECRET", "Blocked: command contains a hardcoded secret", True)
                            break
                    if hit is None:
                        for pat in _DESTRUCTIVE_PATTERNS:
                            if re.search(pat, cmd):
                                hit = ("DESTRUCTIVE", "Blocked: irreversible/destructive operation", True)
                                break
                    if hit:
                        results.append({
                            "target": "command",
                            "input": cmd,
                            "allowed": False,
                            "blocked": True,
                            "check_name": hit[0],
                            "reason": hit[1],
                            "warning_only": False
                        })
                        tracker.record_event(
                            session_id=_mcp_session_id(),
                            event_type=f"GUARD_{hit[0]}",
                            original_tokens=count_tokens(cmd),
                            optimized_tokens=0,
                            details={"cmd": cmd[:100], "reason": hit[1]}
                        )
                    else:
                        # Advisory tier: infinite streams and TLS bypass never
                        # block an inspection, and inspections write no events.
                        advisory = None
                        for pat in _INFINITE_PATTERNS:
                            if re.search(pat, cmd):
                                advisory = ("INFINITE_STREAM", "Command never terminates on its own (follow/streaming) — run it in the background or confirm intent")
                                break
                        if advisory is None:
                            for pat in _TLS_BYPASS_PATTERNS:
                                if re.search(pat, cmd):
                                    advisory = ("TLS_VERIFY_BYPASS", "Certificate verification disabled — confirm this is intentional")
                                    break
                        if advisory:
                            results.append({
                                "target": "command",
                                "input": cmd,
                                "allowed": True,
                                "blocked": False,
                                "check_name": advisory[0],
                                "reason": advisory[1],
                                "warning_only": True
                            })
                except Exception:
                    pass

        if code is not None:
            w_res = TokenSafeGuard.inspect_file_write(file_path, code)
            results.append({
                "target": "code",
                "file_path": file_path,
                "allowed": not w_res.blocked,
                "blocked": w_res.blocked,
                "check_name": w_res.check_name,
                "reason": w_res.reason,
                "warning_only": w_res.warning_only
            })
            if w_res.blocked:
                tracker.record_event(
                    session_id=_mcp_session_id(),
                    event_type=f"GUARD_{w_res.check_name}",
                    original_tokens=count_tokens(code),
                    optimized_tokens=0,
                    details={"target_file": os.path.basename(file_path) if file_path else "unknown", "reason": w_res.reason}
                )

        if not results:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "Error: Please provide 'command' or 'code' (with optional 'file_path') to inspect."}]
            }

        overall_blocked = any(r["blocked"] for r in results)
        payload = {
            "passed": not overall_blocked,
            "blocked": overall_blocked,
            "checks": results
        }
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

    elif name == "slice_diff":
        raw_diff = args.get("diff", "")
        if is_quota_exceeded:
            banner = _quota_banner_if_due(quota, tracker, _mcp_session_id())
            return {"content": [{"type": "text", "text": banner + raw_diff}]}
        max_ctx = int(args.get("max_context", 2))
        sliced, orig_t, opt_t = DiffSlicer.slice_diff(raw_diff, max_context_lines=max_ctx)
        saved = max(0, orig_t - opt_t)
        if saved > 0:
            tracker.record_event(
                session_id=_mcp_session_id(),
                event_type="DIFF_SLICER",
                original_tokens=orig_t,
                optimized_tokens=opt_t,
                details={"saved_tokens": saved, "max_context": max_ctx}
            )
        return {"content": [{"type": "text", "text": sliced}]}

    elif name == "get_token_savings_metrics":
        dashboard = Dashboard(tracker)
        return {"content": [{"type": "text", "text": dashboard.render_markdown()}]}

    elif name == "get_architecture_graph":
        target_abs, err = _resolve_project_root(args)
        if err or not target_abs:
            return {
                "isError": True,
                "content": [{"type": "text", "text": err or "Error: Invalid project_path."}]
            }
        if is_quota_exceeded:
            LicenseManager.should_emit_quota_banner(tracker, session_id=_mcp_session_id(), every_n_turns=5)
            return {"content": [{"type": "text", "text": json.dumps({
                "quota_exceeded": True,
                "monthly_saved": quota.get("monthly_saved"),
                "monthly_limit": quota.get("monthly_limit"),
                "fallback": "Use free list_dir / scoped_search or activate a Solo/Team license: tokensafe activate <key>",
                "warning": quota.get("warning"),
            }, indent=2, ensure_ascii=False)}]}
        graph = GraphEngine.get_instance(target_abs)
        god_nodes = graph.get_god_nodes(7)
        banner = graph.format_topology_banner()
        payload = {
            "project_root": graph.root_dir,
            "banner": banner,
            "total_files": len(graph.file_symbols),
            "total_symbols": len(graph.symbols),
            "god_nodes": god_nodes
        }
        # Cap estimated crawl tokens to max 50,000 to prevent runaway spikes on large/root dirs
        capped_files = min(80, len(graph.file_symbols))
        est_crawl_tokens = min(50000, max(5000, capped_files * 600))
        topo_tokens = 250
        tracker.record_event(
            session_id=_mcp_session_id(),
            event_type="GRAPH_TOPOLOGY",
            original_tokens=est_crawl_tokens,
            optimized_tokens=topo_tokens,
            details={"hubs_count": len(god_nodes), "indexed_files": len(graph.file_symbols)}
        )
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}

    elif name == "get_api_surface":
        target_abs, err = _resolve_project_root(args)
        if err or not target_abs:
            return {
                "isError": True,
                "content": [{"type": "text", "text": err or "Error: Invalid project_path."}]
            }
        if is_quota_exceeded:
            LicenseManager.should_emit_quota_banner(tracker, session_id=_mcp_session_id(), every_n_turns=5)
            return {"content": [{"type": "text", "text": f"{quota.get('warning', '')}\n\nAPI surface map generation is paused over the 500k Community limit. Use scoped_search or list_dir, or activate a subscription at https://tokensafe.dev/#pricing."}]}
        graph = GraphEngine.get_instance(target_abs)
        surface_md = graph.generate_api_surface()
        surface_tokens = count_tokens(surface_md)
        # Cap estimated crawl tokens to max 60,000
        capped_files = min(100, len(graph.file_symbols))
        est_orig_tokens = min(60000, max(10000, capped_files * 600))
        tracker.record_event(
            session_id=_mcp_session_id(),
            event_type="GRAPH_TOPOLOGY",
            original_tokens=est_orig_tokens,
            optimized_tokens=surface_tokens,
            details={"type": "API_SURFACE", "symbols_count": len(graph.symbols)}
        )
        return {"content": [{"type": "text", "text": surface_md}]}

    elif name == "list_dir":
        path = args.get("path") or args.get("project_path") or args.get("directory") or args.get("root_dir") or ""
        if not path:
            return _fallback_error("list_dir", "missing required 'path' argument")

        # Resolve path relative to current working directory or absolute
        abs_path = os.path.abspath(path)
        system_roots = {"/", "/Users", "/home", "/root", os.path.expanduser("~")}
        depth = min(4, max(1, int(args.get("depth", 2))))
        if abs_path in system_roots:
            # Do not walk entire home dir or filesystem root deeply
            depth = 1
        max_entries = min(150, max(10, int(args.get("max_entries", 80))))

        tree_output = build_dir_tree(abs_path, max_depth=depth, max_entries=max_entries)
        tree_tokens = count_tokens(tree_output)
        # Estimate equivalent tokens from multiple verbose ls -la calls (at least ~800 tokens)
        est_orig_tokens = max(800, tree_tokens * 6)
        tracker.record_event(
            session_id=_mcp_session_id(),
            event_type="DIR_LIST",
            original_tokens=est_orig_tokens,
            optimized_tokens=tree_tokens,
            details={"path": os.path.basename(abs_path) or abs_path, "depth": depth, "tokens_saved": est_orig_tokens - tree_tokens}
        )
        return {"content": [{"type": "text", "text": tree_output}]}

    elif name == "scoped_search":
        query = args.get("query", "")
        if not query:
            return _fallback_error("scoped_search", "missing required 'query' argument")

        scope = args.get("scope", "all")
        raw_path = args.get("project_path") or args.get("path") or args.get("root_dir") or args.get("directory")
        if raw_path:
            target_path = os.path.abspath(raw_path)
            if os.path.isfile(target_path):
                target_path = os.path.dirname(target_path)
        else:
            resolved_path, err = _resolve_project_root(args)
            if err or not resolved_path:
                return _fallback_error("scoped_search", err or "unspecified project_path")
            target_path = resolved_path

        max_res = args.get("max_results", 25)
        c_sens = bool(args.get("case_sensitive", False))
        u_regex = bool(args.get("use_regex", False))

        res = ScopedSearch.search(
            query=query,
            project_path=target_path,
            scope=scope,
            max_results=max_res,
            case_sensitive=c_sens,
            use_regex=u_regex
        )
        md_text = res["markdown"]
        if not res.get("success", True):
            return _fallback_error("scoped_search", md_text)

        res_tokens = count_tokens(md_text)
        est_orig = max(1500, res_tokens * 8)
        tracker.record_event(
            session_id=_mcp_session_id(),
            event_type="SCOPED_SEARCH",
            original_tokens=est_orig,
            optimized_tokens=res_tokens,
            details={"query": query, "scope": scope, "matches": res["total_matches"]}
        )
        return {"content": [{"type": "text", "text": md_text}]}

    return _fallback_error(name, f"unknown tool '{name}'")


def _execute_with_timeout(tool_name: str, tool_args: dict, timeout: float = MCP_TOOL_TIMEOUT) -> dict:
    """Runs handle_tool_call inside a daemon thread with hard timeout so stdio MCP never freezes."""
    box = {"result": None, "error": None}

    def _runner():
        try:
            box["result"] = handle_tool_call(tool_name, tool_args)
        except Exception as exc:
            box["error"] = f"{type(exc).__name__}: {str(exc)}"

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=timeout)

    if worker.is_alive():
        return _fallback_error(tool_name, f"execution exceeded {timeout:.1f}s timeout")
    if box["error"] is not None:
        return _fallback_error(tool_name, box["error"])
    if box["result"] is None:
        return _fallback_error(tool_name, "empty result returned")
    return box["result"]


def _get_watched_mtimes() -> Tuple[Optional[float], ...]:
    """Returns mtimes for core server/package files and the update reload marker."""
    try:
        from src.paths import resolve_data_dir
        reload_markers = [os.path.join(resolve_data_dir(), ".mcp_reload")]
    except Exception:
        reload_markers = [os.path.join(BASE_DIR, "data", ".mcp_reload")]
    watched_paths = (
        os.path.abspath(__file__),
        os.path.join(BASE_DIR, "package.json"),
        *reload_markers,
    )
    mtimes: List[Optional[float]] = []
    for p in watched_paths:
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError:
            mtimes.append(None)
    return tuple(mtimes)


def run_mcp_server():
    """Runs stdio JSON-RPC 2.0 loop with per-call timeout, resilient recovery & auto-reload on upgrade."""
    global _MCP_CLIENT_NAME, _TOOLS_LISTED, _EPOCH_GEN
    _MCP_CLIENT_NAME = ""
    _TOOLS_LISTED = False
    _EPOCH_GEN = 0
    _IN_PROCESS_DELIVERED.clear()
    initial_mtimes = _get_watched_mtimes()
    auto_reload = os.environ.get("TOKENSAFE_MCP_AUTORELOAD", "1").strip().lower() not in ("0", "false", "no")

    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # Notifications (no id, or notifications/*) must never receive JSON-RPC responses
        if msg_id is None or (isinstance(method, str) and method.startswith("notifications/")):
            continue

        response = {"jsonrpc": "2.0", "id": msg_id}

        if method == "initialize":
            try:
                client_info = (params or {}).get("clientInfo") or {}
                _set_mcp_client(str(client_info.get("name") or ""))
            except Exception:
                pass
            try:
                from src.telemetry_sync import TelemetrySync
                TelemetrySync.sync_background()
            except Exception:
                pass
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO
            }
        elif method == "tools/list":
            _TOOLS_LISTED = True
            response["result"] = {"tools": TOOLS}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                response["result"] = _execute_with_timeout(tool_name, tool_args)
            except Exception as e:
                response["result"] = _fallback_error(tool_name, f"{type(e).__name__}: {str(e)}")
            finally:
                try:
                    from src.telemetry_sync import TelemetrySync
                    TelemetrySync.sync_background(throttle_seconds=60.0)
                except Exception:
                    pass
        else:
            response["error"] = {"code": -32601, "message": f"Method not found: {method}"}

        try:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except Exception:
            break

        # Safeguard: if TokenSafe files were upgraded on disk while running, exit cleanly
        # immediately after sending the response so the host IDE respawns the new version.
        if auto_reload and method == "tools/call":
            if _get_watched_mtimes() != initial_mtimes:
                break

def main():
    run_mcp_server()

if __name__ == "__main__":
    main()
