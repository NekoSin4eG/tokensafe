"""
Universal Model Context Protocol (MCP) Server for Token Optimizer.
Provides MCP stdio JSON-RPC 2.0 interface compatible with Claude Desktop, Cursor, Windsurf, Cline, and Zed.
Exposes tools for smart file reading, dependency prefetching, prompt compression, and command sanitizing.
"""

import sys
import os
import json
import traceback
from typing import Tuple, Optional

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
from src.agent_firewall import AgentFirewall
from src.diff_slicer import DiffSlicer

SERVER_INFO = {
    "name": "tokensafe-mcp",
    "version": "1.4.0"
}

TOOLS = [
    {
        "name": "smart_read_file",
        "description": "Reads a code file using AST skeletonization and predictive dependency prefetching to save up to 90% context tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the code file"},
                "symbol": {"type": "string", "description": "Optional: targeted function or class name for graph-guided skeletonization"},
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
        "name": "sanitize_command",
        "description": "Rewrites shell commands (git diff, git log, pytest, grep) to prune lockfiles and unbounded logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Raw bash command line"}
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
    }
]

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

def handle_tool_call(name: str, args: dict) -> dict:
    tracker = MetricsTracker()

    if name == "smart_read_file":
        path = args.get("path", "")
        symbol = args.get("symbol", "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        if not path or not os.path.exists(path):
            return {"isError": True, "content": [{"type": "text", "text": f"File not found: {path}"}]}

        # Check Subagent Firewall dedup cache
        is_hit, cached_text, saved_dedup = AgentFirewall.check_cache("mcp_session", path, symbol=symbol or "", tracker=tracker)
        if is_hit and cached_text:
            return {"content": [{"type": "text", "text": cached_text}]}

        # Check loop detector
        is_loop, repeats, msg = LoopDetector.check_loop("mcp_session", "smart_read_file", args)
        if is_loop:
            return {"isError": True, "content": [{"type": "text", "text": f"⚠️ {msg}"}]}

        # Check prefetcher
        prefetch_block, saved_dep = DependencyPrefetcher.prefetch_for_file(path)

        # Check targeted symbol or skeleton
        if start_line is not None and end_line is not None:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            content = "".join(lines[start_line - 1 : end_line])
            return {"content": [{"type": "text", "text": (prefetch_block or "") + content}]}

        if symbol:
            try:
                graph, rel_path = GraphEngine.for_file(path)
                graph_skel = graph.graph_guided_skeletonize(path, target_symbol=symbol)
                if graph_skel:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        raw_code = f.read()
                    orig_t = count_tokens(raw_code)
                    skel_t = count_tokens(graph_skel)
                    saved = max(0, orig_t - skel_t)
                    tracker.record_event(
                        session_id="mcp_session",
                        event_type="GRAPH_SKELETON",
                        original_tokens=orig_t,
                        optimized_tokens=skel_t,
                        details={"file": os.path.basename(path), "symbol": symbol, "saved_tokens": saved}
                    )
                    header = f"// [TokenSafe Graph-Guided Skeleton for '{symbol}' | Full body + 1-hop dependencies]\n"
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
        res = PromptCompactor.compact(prompt, enable_deepseek=True)
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}]}

    elif name == "sanitize_command":
        cmd = args.get("command", "")
        new_cmd, saved, reason = CommandSanitizer.sanitize(cmd)
        result = {
            "original_command": cmd,
            "sanitized_command": new_cmd or cmd,
            "tokens_saved": saved,
            "reason": reason or "No modification needed"
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    elif name == "slice_diff":
        raw_diff = args.get("diff", "")
        max_ctx = int(args.get("max_context", 2))
        sliced, orig_t, opt_t = DiffSlicer.slice_diff(raw_diff, max_context_lines=max_ctx)
        saved = max(0, orig_t - opt_t)
        if saved > 0:
            tracker.record_event(
                session_id="mcp_session",
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
            session_id="mcp_session",
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
        graph = GraphEngine.get_instance(target_abs)
        surface_md = graph.generate_api_surface()
        surface_tokens = count_tokens(surface_md)
        # Cap estimated crawl tokens to max 60,000
        capped_files = min(100, len(graph.file_symbols))
        est_orig_tokens = min(60000, max(10000, capped_files * 600))
        tracker.record_event(
            session_id="mcp_session",
            event_type="GRAPH_TOPOLOGY",
            original_tokens=est_orig_tokens,
            optimized_tokens=surface_tokens,
            details={"type": "API_SURFACE", "symbols_count": len(graph.symbols)}
        )
        return {"content": [{"type": "text", "text": surface_md}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

def run_mcp_server():
    """Runs stdio JSON-RPC 2.0 loop."""
    while True:
        line = sys.stdin.readline()
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

        response = {"jsonrpc": "2.0", "id": msg_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                result = handle_tool_call(tool_name, tool_args)
                response["result"] = result
            except Exception as e:
                response["result"] = {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}]
                }
        else:
            response["error"] = {"code": -32601, "message": f"Method not found: {method}"}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

def main():
    run_mcp_server()

if __name__ == "__main__":
    main()
