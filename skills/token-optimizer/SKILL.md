---
name: token-optimizer
description: >-
  MANDATORY code inspection and architecture navigation skill.
  Activate whenever inspecting files, searching code, locating components/functions/classes,
  reading implementations, analyzing dependencies, or navigating codebase architecture.
  Replaces wasteful terminal grep/find with zero-loss AST smart slicing (-90% tokens),
  extracts definitions with 1-hop dependencies, and tracks token efficiency metrics.
---

# TokenSafe Optimization & Architecture Intelligence Skill

This skill provides autonomous AI coding assistants with deterministic, zero-token AST architecture indexing, graph-guided code slicing, command sanitization, and context optimization metrics.

## MANDATORY CODE INSPECTION DIRECTIVES (ALL AGENTS)

1. **NO RAW TERMINAL GREP OR FIND**:
   - Strictly NEVER run `grep`, `egrep`, `ripgrep`, or `find` in `run_command` on source code files (`.ts`, `.tsx`, `.py`, `.js`, `src/`, `views/`, `components/`).
   - Raw code searches via bash burn thousands of context tokens and are blocked by TokenSafe Guard.

2. **SYMBOL & COMPONENT EXTRACTION (`smart_read_file`)**:
   - When locating or reading any function, component, class, or handler (e.g. `<LuckyWheelTab>`, `areSectorEqual`, `useWallet`), ALWAYS call `tokensafe:smart_read_file` with `path` and `symbol`.
   - Returns the exact target definition and 1-hop dependencies in full while folding surrounding inert code into lightweight signatures (-85% to -92% tokens).

3. **ARCHITECTURE SURVEY BEFORE BROWSING (`get_architecture_graph`)**:
   - Before multi-file exploration or asking where things are located, ALWAYS call `tokensafe:get_architecture_graph(project_path)`.
   - Returns top architectural God Nodes and dependency topology in < 250 tokens.

4. **API SURFACE INSPECTION (`get_api_surface`)**:
   - Call `tokensafe:get_api_surface(project_path)` to get all exported classes, types, interfaces, and function signatures in a single compact markdown map.

## Core MCP Tools (`tokensafe`)

| MCP Tool | Purpose | Token Cost |
|---|---|---|
| `tokensafe:get_architecture_graph` | Analyzes AST topology, God Nodes, and hub centrality for `project_path`. | ~150-250 tokens |
| `tokensafe:get_api_surface` | Exports public signatures and types for the entire project. | ~300-600 tokens |
| `tokensafe:smart_read_file` | Reads `path` focusing on `symbol` + 1-hop dependencies; folds inert code into signatures. | -85% to -92% vs raw read |
| `tokensafe:sanitize_command` | Strips lockfiles, limits test/build output, and prevents context poisoning. | Saves 10k-50k tokens |
| `tokensafe:slice_diff` | Slices unified git diffs, eliminates lockfiles, and minimizes hunk context lines. | -50% to -80% |
| `tokensafe:compress_prompt` | Compresses verbose user instructions into lean TOCP S-expressions. | -40% to -65% |
| `tokensafe:get_token_savings_metrics` | Returns total tokens saved, USD value, and breakdown by source. | ~100 tokens |

## Agent Graph-First Workflow

### 1. Zero-Token Architecture Survey
Before running recursive searches or reading multi-file structures, query the architecture graph:
```json
{
  "ServerName": "tokensafe",
  "ToolName": "get_architecture_graph",
  "Arguments": {
    "project_path": "/absolute/path/to/project"
  }
}
```
* **Output**: Centrality scores, dependent counts, and identified God Nodes (e.g., `src/graph_engine.py (71 deps)`).

### 2. Graph-Guided Symbol Inspection
To read a targeted function or class without reading hundreds of lines of inert code:
```json
{
  "ServerName": "tokensafe",
  "ToolName": "smart_read_file",
  "Arguments": {
    "path": "/absolute/path/to/file.py",
    "symbol": "TargetClassName"
  }
}
```
* **Output**: The target definition and its direct 1-hop callees are shown in full; all other classes/methods are folded into 1-line signatures.

### 3. Command Sanitization
Before executing potentially verbose terminal commands (`git diff`, `git grep`, `pytest`, `find`):
```json
{
  "ServerName": "tokensafe",
  "ToolName": "sanitize_command",
  "Arguments": {
    "command": "git diff HEAD~1"
  }
}
```

## CLI Usage

Developers and agents can run `tokensafe` directly from the terminal:

```bash
# View terminal breakdown & recent optimizations
tokensafe stats

# Open interactive Apple-style HTML dashboard in browser
tokensafe html

# Run universal agent installer/synchronizer
tokensafe install

# Test prompt compression
tokensafe test "Refactor get_user_balance with redis caching"
```
