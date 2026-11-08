---
name: token-optimizer
description: >-
  Inspect token efficiency metrics, view cost savings dashboard, test prompt compression,
  navigate codebases via zero-token AST architecture graphs, and operate the Token-Optimized Communication Protocol (TOCP).
---

# TokenSafe Optimization & Architecture Intelligence Skill

This skill provides autonomous AI coding assistants with deterministic, zero-token AST architecture indexing, graph-guided code slicing, command sanitization, and context optimization metrics.

## When to Activate This Skill
1. **Architecture Discovery**: Mapping new or unfamiliar codebases, finding God Nodes and key dependency hubs without LLM token burn.
2. **Graph-Guided Code Inspection**: Reading functions or classes with their 1-hop dependencies while folding inert boilerplate (-85% to -92% tokens).
3. **Telemetry & Economics**: Inspecting token savings, breakdown by optimization source, and ROI.
4. **Command & Prompt Optimization**: Sanitizing terminal output and testing TOCP compression.

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

Developers and agents can run `tokensafe` (or `./bin/agy-tokens`) directly from the terminal:

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
