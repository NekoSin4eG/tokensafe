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
