---
name: token-optimizer
description: >-
  Inspect token efficiency metrics, view cost savings dashboard, test prompt compression,
  and operate the Token-Optimized Communication Protocol (TOCP) in Antigravity.
---

# Token Optimizer Skill

This skill allows the agent to inspect token savings, query the metrics database,
and report resource utilization efficiency.

## Quick CLI Commands

Use the bundled `agy-tokens` CLI from this plugin directory:

```bash
# Display visual terminal dashboard
./bin/agy-tokens stats

# Test compression on a prompt and view savings
./bin/agy-tokens test "Слушай, перепиши функцию get_balance в wallet.ts, добавь redis cache"

# Export Markdown summary for artifacts or chat
./bin/agy-tokens markdown

# Reset metrics database
./bin/agy-tokens reset
```

## Programmatic Usage

To inspect current metrics programmatically:

```python
from src.metrics import MetricsTracker
from src.dashboard import Dashboard

tracker = MetricsTracker()
dashboard = Dashboard(tracker)
print(dashboard.render_markdown())
```

## How It Works Under the Hood
1. **PreInvocation Hook**: Intercepts verbose user prompts and generates a concise S-expression `(ACTION target symbol [traits] "intent")`, stripping politeness filler.
2. **PreToolUse Hook**: Prevents dumping 800+ lines into the context window by slicing `read_file` to relevant line ranges.
3. **Prompt Cache Anchor**: Static TOCP rules in `rules/AGENTS.md` hit Gemini's prompt cache with 100% stability.
4. **Persistent Metrics**: Every token saved is recorded in SQLite (`data/metrics.db`) with timestamp, percentage reduction, and estimated USD value.
