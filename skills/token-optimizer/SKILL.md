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
   - **1-Hop vs Surgical**: By default, `smart_read_file` includes direct 1-hop dependencies so you understand called helper functions without extra tool calls. When you only need the target method for a surgical edit, pass `"include_deps": false` to get pure single-method isolation (-95% tokens).
   - **Result**: Preserves the exact target definition while folding surrounding inert code into clean 1-line signatures without docstrings (-85% to -95% tokens).

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

The full tool table with efficiency impact, JSON workflow examples, the Guard check matrix, and the CLI reference live in `references/` — read only the file you need:

- [`references/mcp-tools.md`](references/mcp-tools.md) — Core MCP Tools table + Agent Workflow JSON examples
- [`references/guard-checks.md`](references/guard-checks.md) — Security & Hygiene Guard check matrix (deny/ask per operation)
- [`references/cli-reference.md`](references/cli-reference.md) — `tokensafe` CLI command reference

Quick pointers:

- Search: `tokensafe:scoped_search(query, scope)` — never raw grep.
- Inspect: `tokensafe:smart_read_file(path, symbol)` — definition + 1-hop deps.
- Navigate: `tokensafe:get_architecture_graph(project_path)` / `tokensafe:get_api_surface(project_path)`.
- Context: `tokensafe:compact_conversation(transcript_path)` / `tokensafe:context_budget`.

---

## Context Governor & History Compactor

For long-running autonomous sessions, TokenSafe actively manages context bloat:

1. **Multi-Turn History Compaction**:
   - At conversation turn >= 3, automatically compiles past turns into a lean **Consolidated State Ledger**.
   - Preserves active file paths, modified symbols, and pending tasks while discarding transient tool output noise.
   - **Manual trigger**: call `tokensafe:compact_conversation` with the session's `transcript_path` any time you need to shed context between subtasks — it returns the ledger JSON plus exact original/folded token counts.
2. **Subagent Deduplication Firewall**:
   - Caches file reads across subagents. If subagent B attempts to read a file already inspected by subagent A, returns the cached skeleton instantly.
3. **Loop Guard**:
   - Detects repeated identical tool calls and triggers `force_ask` to prevent runaway infinite agent loops.
4. **Output Token Guard**:
   - Intercepts wholesale `write_file` calls on existing files with >= 40 lines to prevent expensive output token burn.

---

## Proof-of-Savings Turn Footer (always in English)

At the end of any response where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`

The USD figure is **derived** (tokens × $1.5/M blended rate) — an approximation for planning, never a bill. Cumulative analytics distinguish `measured` (metered I/O delta) from `estimated` (counterfactual baseline) savings — see METRICS.md.
