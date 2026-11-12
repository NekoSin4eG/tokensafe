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
   - **1-Hop Dependency Modes (`dep_mode` & `include_deps`)**: By default (`dep_mode="signature"`), `smart_read_file` returns the full target implementation plus compact 1-line 1-hop dependency signatures and collapsed imports (-95%+ tokens). Pass `"dep_mode": "full"` when full 1-hop dependency bodies are required, or `"include_deps": false` for 0-hop surgical isolation.
   - **Cache Pointer Recovery (`force_full`)**: When repeating an unchanged read in the same MCP session, `smart_read_file` may return a compact pointer referencing the prior output. Reuse that earlier output; only pass `"force_full": true` if your context was compacted or you are a fresh subagent lacking prior turns.
   - **Multi-Symbol Bundles (`smart_bundle`)**: When you need 3+ related symbols across several files, call `tokensafe:smart_bundle(targets=[{path, symbol}])` ONCE instead of chained `smart_read_file` calls (max 8 targets). It merges per-file skeletons, deduplicates shared import/type lines into one block, and unifies 1-hop dependency signatures — one roundtrip instead of many.
   - **Schema Auto-Slicing**: OpenAPI/Swagger, Prisma, GraphQL and SQL files bypass AST skeletonization. Pass the spec path (`/users/{id}`), Prisma model, GraphQL type, or SQL table as `symbol` for a targeted slice with 1-hop references; an empty `symbol` returns a compact overview (paths/models/types/tables index) instead of a 20k-50k token dump.
   - **Result**: Preserves the exact target definition while folding surrounding inert code into clean 1-line signatures without docstrings (-85% to -98% tokens).

3. **ARCHITECTURE SURVEY BEFORE BROWSING (`get_architecture_graph`)**:
   - Before multi-file exploration or asking where things are located, ALWAYS call `tokensafe:get_architecture_graph(project_path)`.
   - **Result**: Returns top architectural God Nodes, dependency hubs, and centrality rankings in < 250 tokens without file crawling.

4. **API SURFACE INSPECTION (`get_api_surface`)**:
   - Call `tokensafe:get_api_surface(project_path)` to get all exported classes, types, interfaces, and function signatures in a single compact markdown map.

5. **SURGICAL FILE EDITS (`edit_file`)**:
   - On existing files with >= 40 lines, wholesale rewrites via `write_file` are blocked by **Output Guard**. Always use `edit_file` with precise `Target` and `Replacement` blocks.

6. **SECRET LEAK PREVENTION (ZERO HARDCODED FALLBACKS)**:
   - Never provide real private keys, API secrets, or credentials as fallback values in `os.environ.get()` or `process.env`. Real credentials must exist strictly in environment variables.

7. **CONTEXT BUDGET BEFORE HEAVY WORK (`context_budget` → `compact_conversation`)**:
   - Before large multi-step tasks, call `tokensafe:context_budget(model=...)` to check the window budget (tools/history/prompts breakdown, 50/80/95% thresholds).
   - When it reports `warning` or `critical`, call `tokensafe:compact_conversation(transcript_path=...)` to fold past turns into a State Ledger before continuing — do not let context exhaustion degrade code quality mid-task.

---

## Core MCP Tools (`tokensafe`)

The full tool table with efficiency impact, JSON workflow examples, the Guard check matrix, and the CLI reference live in `references/` — read only the file you need:

- [`references/mcp-tools.md`](references/mcp-tools.md) — Core MCP Tools table + Agent Workflow JSON examples
- [`references/guard-checks.md`](references/guard-checks.md) — Security & Hygiene Guard check matrix (deny/ask per operation)
- [`references/cli-reference.md`](references/cli-reference.md) — `tokensafe` CLI command reference

Quick pointers:

- Search: `tokensafe:scoped_search(query, scope)` — never raw grep.
- Inspect: `tokensafe:smart_read_file(path, symbol)` — definition + 1-hop deps.
- Bundle: `tokensafe:smart_bundle(targets=[{path, symbol}])` — 3-8 related symbols across files in one call, shared imports deduplicated.
- Navigate: `tokensafe:get_architecture_graph(project_path)` / `tokensafe:get_api_surface(project_path)`.
- Directory: `tokensafe:list_dir(path, depth=2)` — clean tree, never verbose `ls -la`.
- Terminal: `tokensafe:sanitize_command(command, cwd)` — bounded logs/diffs, test runner guards.
- Context: `tokensafe:compact_conversation(transcript_path)` / `tokensafe:context_budget`.

---

## Context Governor & History Compactor

For long-running autonomous sessions, TokenSafe actively manages context bloat:

1. **Multi-Turn History Compaction**:
   - At conversation turn >= 3, automatically compiles past turns into a lean **Consolidated State Ledger**.
   - Preserves active file paths, modified symbols, and pending tasks while discarding transient tool output noise.
   - **Manual trigger**: call `tokensafe:compact_conversation` with the session's `transcript_path` any time you need to shed context between subtasks — it returns the ledger JSON plus exact original/folded token counts.
2. **Subagent Deduplication Firewall**:
   - Caches file reads across subagents and sessions using SHA-256 content hashes. Interactive MCP clients receive self-healing pointer hits on unchanged repeat reads inside the same session; fresh subagents or post-compaction callers recover automatically with `force_full=true`.
3. **Loop Guard**:
   - Detects repeated identical tool calls and triggers `force_ask` to prevent runaway infinite agent loops.
4. **Output Token Guard**:
   - Intercepts wholesale `write_file` calls on existing files with >= 40 lines to prevent expensive output token burn.

---

## LLM Prompt Cache Optimization

TokenSafe actively maximizes provider KV-cache hit rates alongside token compression:

1. **Append-Only Epoch Architecture**: In proxy mode, past messages (`messages[0..N-2]`) are never retroactively mutated within an epoch, preserving prefix cache hits. When cumulative context reaches **80% of the model's window**, an atomic Epoch Rollover consolidates deep history into a State Ledger.
2. **Multi-Breakpoint Cache Control**: `PromptCacheNormalizer` sorts `tools` deterministically and places Anthropic `cache_control: {"type": "ephemeral"}` across tools, static system instructions, and the penultimate completed turn; OpenAI requests receive deterministic `prompt_cache_key`.
3. **Deterministic Prefix Normalization**: Ephemeral timestamps, hit counters, and session IDs are stripped from static prefixes, and `AgentFirewall` headers remain byte-deterministic across hits.
4. **Parametric Prompt Template Caching**: `DistillationCache` extracts file paths and symbols into positional slots (`<FILE_0>`, `<SYM_0>`) so structurally identical prompts hit L1/L2/L3 caches with 0 LLM roundtrips.

---

## Proof-of-Savings Turn Footer (always in English)

At the end of any response where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`

The USD figure is **derived** (tokens × $1.5/M blended rate) — an approximation for planning, never a bill. Cumulative analytics distinguish `measured` (metered I/O delta) from `estimated` (counterfactual baseline) savings — see METRICS.md.
