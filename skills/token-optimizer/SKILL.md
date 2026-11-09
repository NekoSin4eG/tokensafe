---
name: token-optimizer
description: >-
  MANDATORY code inspection and architecture navigation skill.
  Activate whenever inspecting files, searching code, locating components/functions/classes,
  reading implementations, analyzing dependencies, or navigating codebase architecture.
  Replaces wasteful terminal grep/find with zero-loss AST smart slicing (-90% tokens),
  extracts definitions with 1-hop dependencies, enforces security guards, and tracks token efficiency metrics.
---

# TokenSafe Optimization & Architecture Intelligence Skill

TokenSafe is the zero-loss token economy, context governor, and security engine for autonomous AI coding agents.
It replaces brute-force terminal crawls (`grep`, `find`, `cat`) with deterministic AST symbol extraction,
folds multi-turn conversation history, slices verbose tool outputs, and guards against secret leaks and destructive actions.

---

## MANDATORY AGENT DIRECTIVES

1. **ZERO RAW TERMINAL GREP / FIND (HARD-BLOCKED)**:
   - Strictly NEVER run `grep`, `egrep`, `ripgrep`, or `find` in `run_command` on source code files (`.ts`, `.tsx`, `.py`, `.js`, `src/`, `views/`, `components/`).
   - Raw code searches via bash burn thousands of context tokens and are blocked by **TokenSafe Guard** (`decision: deny`).

2. **SYMBOL & COMPONENT EXTRACTION (`smart_read_file`)**:
   - When locating or reading any function, component, class, or handler (e.g. `<LuckyWheelTab>`, `areSectorEqual`, `useWallet`), ALWAYS call `tokensafe:smart_read_file` with `path` and `symbol`.
   - **Result**: Preserves the exact target definition and its direct 1-hop dependencies while folding surrounding inert code into 1-line signatures (-85% to -92% tokens).

3. **ARCHITECTURE SURVEY BEFORE BROWSING (`get_architecture_graph`)**:
   - Before multi-file exploration or asking where things are located, ALWAYS call `tokensafe:get_architecture_graph(project_path)`.
   - **Result**: Returns top architectural God Nodes, dependency hubs, and centrality rankings in < 250 tokens without file crawling.

4. **API SURFACE INSPECTION (`get_api_surface`)**:
   - Call `tokensafe:get_api_surface(project_path)` to get all exported classes, types, interfaces, and function signatures in a single compact markdown map.

5. **SURGICAL FILE EDITS (`edit_file`)**:
   - On existing files with >= 40 lines, wholesale rewrites via `write_file` are blocked by **Output Guard**. Always use `edit_file` with precise `Target` and `Replacement` blocks.

6. **SECRET LEAK PREVENTION (ZERO HARDCODED FALLBACKS)**:
   - Never provide real private keys, API secrets, or credentials as fallback values in `os.environ.get()` or `process.env`. Real credentials must exist strictly in environment variables.

---

## Core MCP Tools (`tokensafe`)

All tools are exposed via Model Context Protocol (stdio JSON-RPC 2.0).

| MCP Tool | Description | Efficiency Impact |
|---|---|---|
| `tokensafe:smart_read_file` | Graph-guided AST reader with target `symbol` body retention and 1-hop dependency inlining. | **-85% to -92%** token reduction |
| `tokensafe:get_architecture_graph` | Computes dependency topology, hub centrality, and identifies top God Nodes for `project_path`. | **~200 tokens** (replaces 50k crawl) |
| `tokensafe:get_api_surface` | Generates a compact markdown map of all exported types, interfaces, and classes. | **~500 tokens** for entire project |
| `tokensafe:sanitize_command` | Intercepts shell commands to strip lockfiles, cap unbounded git logs, and quiet test tracebacks. | Saves **10k-50k tokens** per run |
| `tokensafe:slice_diff` | Slices unified git diffs, eliminating lockfiles and minimizing hunk context lines. | **-50% to -80%** diff size |
| `tokensafe:compress_prompt` | Compresses verbose user instructions into lean TOCP S-expressions via DeepSeek/Regex. | **-40% to -65%** prompt tokens |
| `tokensafe:get_token_savings_metrics` | Returns cumulative analytics: tokens saved, context ratio, USD value, and optimization source breakdown. | Persistent SQLite analytics |

---

## Agent Workflows

### 1. Architecture Exploration Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "get_architecture_graph",
  "Arguments": {
    "project_path": "/Users/.../projects/my-app"
  }
}
```
*Identifies core architectural hubs and dependency weights before inspecting files.*

### 2. Targeted Symbol Inspection Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "smart_read_file",
  "Arguments": {
    "path": "/Users/.../projects/my-app/src/views/StatsView.tsx",
    "symbol": "LuckyWheelTab"
  }
}
```
*Extracts only the `LuckyWheelTab` implementation and its direct sub-components with inert code folded.*

### 3. Public API Map Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "get_api_surface",
  "Arguments": {
    "project_path": "/Users/.../projects/my-app"
  }
}
```
*Returns all exported interfaces and types without reading hundreds of module files.*

---

## Security & Hygiene Engine (TokenSafe Guard)

TokenSafe Guard automatically intercepts dangerous operations in `PreToolUse`:

| Guard Check | Monitored Operation | Action Taken |
|---|---|---|
| **RAW_CODE_SEARCH** | Terminal `grep`, `egrep`, `rg`, `find` on source code | **Blocked** (`decision: deny`), directs to `smart_read_file` |
| **SECRET_FALLBACK** | Hardcoded secrets in `os.environ.get()` / `process.env` | **Blocked** (`decision: deny`), prevents credential commit |
| **TLS_VERIFY_BYPASS** | Insecure TLS bypass (`verify=False` / `rejectUnauthorized: false`) | **Blocked** (`decision: deny`), MitM attack prevention |
| **SQL_INJECTION** | Unsafe f-string / template string interpolation in queries | **Blocked** (`decision: deny`), enforces parameterized queries |
| **GIT_SECRET_STAGING** | `git add .env` or blanket staging with unignored secrets | **Blocked** (`decision: deny`), prevents staging credentials |
| **DESTRUCTIVE_RM** | `rm -rf /`, `rm -rf ~`, `rm -rf *` | **Blocked** (`decision: deny`), prevents catastrophic deletion |
| **DATA_EXFILTRATION** | Piping `.env` or private keys to `curl`/`wget` | **Blocked** (`decision: deny`), prevents secret leakage |
| **PACKAGE_TYPOSQUAT** | Installing known typosquatted packages | **Warning** injected into turn context |

---

## Context Governor & History Compactor

For long-running autonomous sessions, TokenSafe actively manages context bloat:

1. **Multi-Turn History Compaction**:
   - At conversation turn >= 3, automatically compiles past turns into a lean **Consolidated State Ledger**.
   - Preserves active file paths, modified symbols, and pending tasks while discarding transient tool output noise.
2. **Subagent Deduplication Firewall**:
   - Caches file reads across subagents. If subagent B attempts to read a file already inspected by subagent A, returns the cached skeleton instantly.
3. **Loop Guard**:
   - Detects repeated identical tool calls and triggers `force_ask` to prevent runaway infinite agent loops.
4. **Output Token Guard**:
   - Intercepts wholesale `write_file` calls on existing files with >= 40 lines to prevent expensive output token burn.

---

## CLI Reference (`tokensafe`)

Run standalone in the terminal or invoke via agent:

```bash
# Telemetry & Economics
tokensafe stats               # View terminal report with tokens saved & USD ROI
tokensafe html                # Launch interactive Apple-style HTML dashboard
tokensafe benchmark           # Run zero-loss SWE-bench benchmark suite

# Code Intelligence & Architecture
tokensafe graph [path]        # Inspect God Nodes and centrality ranking
tokensafe map [path]          # Generate tokensafe-out/API_SURFACE.md
tokensafe skeleton <file>     # View AST skeleton for a specific file

# Multi-Agent Ecosystem Management
tokensafe install             # Interactive agent installer (Cursor, Windsurf, Claude, etc.)
tokensafe install --all       # Automatically configure all 9 detected agent environments
tokensafe update              # One-click self-update & multi-agent re-synchronization

# System Health & Hygiene
tokensafe doctor              # Audit MCP tool overhead and token tax
tokensafe prune               # Compact metrics database and prune old events
tokensafe vacuum              # Reclaim SQLite database disk space
tokensafe test "<prompt>"     # Test TOCP S-expression prompt compaction
```

---

## Proof-of-Savings Turn Footer

At the end of any response where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens ($<usd>)`

