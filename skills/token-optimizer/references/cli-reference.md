# CLI Reference (`tokensafe`)

Run standalone in the terminal or invoke via agent:

```bash
tokensafe --version           # Print version and exit (no metrics side effects)

# Telemetry & Economics
tokensafe stats               # View terminal report with tokens saved & USD ROI
tokensafe html                # Launch interactive Apple-style HTML dashboard
tokensafe benchmark           # Run the zero-context-loss benchmark suite (simulated SWE-bench harness; see BENCHMARK_REPORT.md)
tokensafe tests [pytest]      # Run the unit test suite (compact failure report; --traceback for full stacks)

# Code Intelligence & Architecture
tokensafe graph [path]        # Inspect God Nodes and centrality ranking
tokensafe map [path]          # Generate tokensafe-out/API_SURFACE.md
tokensafe skeleton <file>     # View AST skeleton for a specific file

# Multi-Agent Ecosystem Management
tokensafe install             # Interactive agent installer (Cursor, Windsurf, Claude, etc.)
tokensafe install --all       # Automatically configure all 9 detected agent environments
tokensafe update              # One-click self-update & multi-agent re-synchronization

# System Health & Hygiene
tokensafe guard "<command>"   # Pre-flight check command safety (rm, secrets, grep)
tokensafe guard audit [path]  # Recursively audit project files for security violations
tokensafe doctor              # Audit MCP tool overhead and token tax
tokensafe prune               # Compact metrics database and prune old events
tokensafe vacuum              # Reclaim SQLite database disk space
tokensafe test "<prompt>"     # Test TOCP S-expression prompt compaction
```
