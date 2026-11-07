# ⚡ Antigravity Token Optimizer (`agy-token-optimizer`)

> **Breakthrough resource utilization and token economy plugin for Google Antigravity (AGY).**  
> Reduces LLM context consumption by up to **80%** using the **Token-Optimized Communication Protocol (TOCP)**, AST tool slicing, and real-time SQLite metrics tracking.

---

## 🚀 The Core Breakthrough

Traditional prompts and agent tool calls suffer from two massive token drains:
1. **The BPE Tokenizer Penalty:** Natural languages like Russian, Chinese, or Spanish are penalized by Byte-Pair Encoding (BPE), costing 1.5–2.5× more tokens per word than English. Courteous introductions ("Слушай, перепиши пожалуйста функцию...") add pure noise.
2. **Context Window Bloat from Tools:** 70%+ of agent tokens are burned by dumping whole files (800+ lines) or hundreds of lines of noisy `node_modules` stack traces.

### How this plugin solves both:
- **PreInvocation Hook:** Strips filler and compiles prompts into ultra-dense **TOCP S-Expressions** (`(REFACTOR wallet.ts get_balance [async redis])`), slashing prompt tokens by **40%–70%**.
- **PreToolUse Hook:** Automatically intercepts large `read_file` calls and applies AST/symbol slicing, cutting a 5,000-token file dump down to a 350-token window (**-90%**).
- **Prompt Caching Anchor:** Static rules in `rules/AGENTS.md` hit Gemini's prompt cache with 100% stability.
- **SQLite Analytics Engine:** Tracks every single token and dollar saved with a visual terminal dashboard.

---

## 📊 Live Metrics & Dashboard

The plugin includes a dedicated CLI utility: `agy-tokens`.

### Terminal Dashboard
```bash
./bin/agy-tokens stats
```
```text
╔════════════════════════════════════════════════════════════════════╗
║           ⚡ ANTIGRAVITY TOKEN OPTIMIZER DASHBOARD ⚡            ║
╚════════════════════════════════════════════════════════════════════╝

  Total Tokens Saved:  3,028 tokens
  Efficiency Gain:     [███████████████████░░░░░] 82.8%
  Estimated Savings:   $0.0045 USD
  Context Traffic:     3,655 raw ➔ 627 optimized (2 events)

── Breakdown by Source ──────────────────────────────────────────────
  • Prompt Compactor (TOCP)      : 22 tokens saved (1 events)
  • File Tool Slicer (AST)       : 3,006 tokens saved (1 events)

── Recent Optimizations ─────────────────────────────────────────────
  [01:36:28] TOOL_READ      (wallet.ts)          ➔ +3,006 saved (83.1%)
  [01:36:21] PROMPT         (REFACTOR)           ➔ +22 saved (61.1%)
```

### Test Compression on Any Text
```bash
./bin/agy-tokens test "Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной"
```
```text
Original Input:  Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной
Compacted DSL:   (REFACTOR wallet.ts get_balance [async redis cache] )

Original Tokens:  51
Compacted Tokens: 20
Tokens Saved:     +31 tokens ([██████████████░░░░░░░░░░] 60.8%)
```

### Export Markdown Report
```bash
./bin/agy-tokens markdown
```

---

## 📂 Project Structure

```text
agy-token-optimizer/
├── plugin.json                 # Antigravity plugin manifest
├── hooks.json                  # Lifecycle hook declarations (PreInvocation, PreToolUse, PostToolUse)
├── rules/
│   └── AGENTS.md               # TOCP Protocol directives for LLM (prompt-cached)
├── skills/
│   └── token-optimizer/
│       └── SKILL.md            # On-demand Antigravity skill
├── proto/
│   └── tocp.proto              # Protocol Buffers contract
├── src/
│   ├── tokenizer.py            # Zero-dependency calibrated BPE token counter
│   ├── compactor.py            # Semantic compactor (Natural Language -> TOCP S-Expr)
│   ├── slicer.py               # AST & Line windowing tool optimizer
│   ├── metrics.py              # Persistent SQLite metrics engine
│   └── dashboard.py            # Visual ANSI terminal & Markdown generator
├── hooks/
│   ├── pre_invocation.py       # PreInvocation hook entry point
│   ├── pre_tool_use.py         # PreToolUse hook entry point
│   └── post_tool_use.py        # PostToolUse hook entry point
├── bin/
│   └── agy-tokens              # Executable CLI
├── tests/                      # Unit test suite (100% passing)
├── install.sh                  # One-click installer for Antigravity
└── README.md
```

---

## 🛠️ Installation & Antigravity Setup

Run the installer:
```bash
./install.sh
```
This symlinks the plugin directly into `~/.gemini/config/plugins/agy-token-optimizer`.  
Antigravity automatically loads it on startup.

---

## 🧪 Running Unit Tests

```bash
python3 -m unittest discover tests
```
No external packages required — runs on standard Python 3.10+.
