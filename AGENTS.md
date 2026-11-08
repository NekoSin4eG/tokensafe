# Antigravity Token Optimizer Project Instructions

Welcome to the **agy-token-optimizer** project repository.

## Project Overview
This repository implements a high-performance token economy plugin for Google Antigravity (AGY).
It intercepts user prompts via lifecycle hooks, compiles natural language into lean TOCP S-expressions,
slices bulky tool calls (such as full-file reads), and provides real-time SQLite metrics tracking.

## Architecture Guidelines
- `src/tokenizer.py`: Zero-dependency, calibrated BPE token counter.
- `src/compactor.py`: Semantic intent extractor and S-expression serializer.
- `src/slicer.py`: Tool arguments optimizer (AST & line windowing).
- `src/metrics.py`: SQLite metrics persistence engine (`data/metrics.db`).
- `src/dashboard.py`: Visual terminal & Markdown analytics visualizer.
- `hooks/`: Lifecycle hook implementations (`PreInvocation`, `PreToolUse`, `PostToolUse`).
- `bin/agy-tokens`: Standalone executable CLI for developers.

## Testing & Quality
Run unit tests with:
```bash
python3 -m unittest discover tests
```
Before submitting changes, ensure zero external runtime dependencies are introduced (standard library only for maximum portability).

## Agent Code Inspection Protocol (TokenSafe MCP)
- **Architecture Navigation**: Always call `tokensafe:get_architecture_graph` before grep or multi-file reading.
- **Symbol Inspection**: Always call `tokensafe:smart_read_file` with `symbol` specified to retrieve graph-guided slices with 1-hop dependencies.
- **Raw Grep**: Strictly prohibited for architectural surveys. Use `tokensafe-out/API_SURFACE.md` or `get_architecture_graph`.

## Response Formatting Directive (Proof-of-Savings Footer)
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens`
