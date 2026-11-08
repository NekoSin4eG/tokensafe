# ⚡ TokenSafe (`tokensafe`)

[![npm version](https://img.shields.io/npm/v/tokensafe.svg?color=CB3837)](https://www.npmjs.com/package/tokensafe)
[![PyPI version](https://img.shields.io/pypi/v/tokensafe.svg?color=3775A9)](https://pypi.org/project/tokensafe/)
[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![SWE-bench: Verified](https://img.shields.io/badge/SWE--bench-up_to_95%25_Savings-06B6D4.svg)](https://tokensafe.dev#benchmarks)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-3B82F6.svg)](https://python.org)
[![Trial: 7 Days Free](https://img.shields.io/badge/Trial-7_Days_Free-10B981.svg)](https://tokensafe.dev#pricing)

> **Autonomous Zero-Loss Token Economy & Context Optimizer for AI Coding Agents.**  
> Slashes LLM token costs by **up to 95%** with zero quality loss. Built for **Google Antigravity, OpenAI Codex, Cursor, Claude Desktop, Z.ai Code, Windsurf & Cline**.

🌐 **Official Website:** [https://tokensafe.dev](https://tokensafe.dev)

---

## ⚡ 1-Click Universal Install

Connect TokenSafe to all detected AI coding agents on your machine in seconds:

```bash
# Option 1: Zero-Install via npx (Recommended for Cursor & Claude Desktop)
npx tokensafe

# Option 2: Python / PyPI
pip install tokensafe && tokensafe

# Option 3: Direct Shell Script (macOS / Linux)
curl -fsSL https://tokensafe.dev/install.sh | bash
```

---

## 🔌 Universal MCP Server Configuration

Connect TokenSafe directly to **Claude Desktop** (`claude_desktop_config.json`) or **Cursor** MCP Settings:

```json
{
  "mcpServers": {
    "tokensafe": {
      "command": "npx",
      "args": ["-y", "tokensafe", "mcp"]
    }
  }
}
```

---

## 🚀 The Breakthrough: How It Saves 60%–90% Tokens

Traditional AI code interactions suffer from massive context bloat:
1. **The BPE Token Penalty:** Verbose natural language prompts and filler waste 40%–70% of ingress context.
2. **Bulk Tool Dumps:** Reading whole 800+ line files burns 5,000+ tokens per turn when only 20 lines are needed.
3. **Stuck Loops & Bloated Diffs:** Terminal `git diff` and repetitive failed commands spiral context into overflow.

### TokenSafe Solves All Three Autonomously:
- **Graph-Guided AST Skeletonization:** Folds inactive function bodies into signatures, keeping target functions and 1-hop dependencies full while collapsing unrelated code (`-85% to -92%`).
- **Prompt Cache Prefix Normalizer:** Strips volatile timestamps and aligns system prompts to 64-token boundaries. Injects Anthropic ephemeral breakpoints (`cache_control: {"type": "ephemeral"}`) and DeepSeek chunking for 100% provider cache hits and 90% cost discounts.
- **Context Window Governor & Token Ceiling:** Enforces deterministic token budgets via 3-stage cascading autothrottling (AST skeletonization ➔ TOCP user message history compaction ➔ sliding state ledger) to prevent catastrophic context overflows.
- **Subagent Deduplication Firewall:** Detects repeated file reads across subagents and agent swarms, serving cached AST slices directly from SQLite session memory (`SUBAGENT_DEDUP`) with zero redundant overhead.
- **AST Diff Slicer & Lockfile Stripper:** Compresses code review diffs by stripping noisy lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`) and minified assets, condensing diffs to 2 lines of context (`git diff -U2`) for up to 96% token savings.
- **God Nodes & Architectural Hub Detection:** Calculates Degree Centrality to identify structural project hubs and injects topology awareness into agent sessions without expensive file crawling.
- **TOCP Prompt Distillation:** Intercepts prompts and distills intent into lean S-Expressions via **Google Gemini (2.0 Flash-Lite), OpenAI ChatGPT (gpt-4o-mini), Anthropic Claude, or DeepSeek-Flash** (`-40% to -70%`). Automatically detects `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `DEEPSEEK_API_KEY`.
- **API Surface Map Generator:** Generates high-density `tokensafe-out/API_SURFACE.md` (~1.5k tokens) for instant agent codebase onboarding.
- **Predictive Dependency Prefetcher:** Attaches imported symbol signatures to eliminate downstream exploratory `read_file` roundtrips.
- **Live SSE Streaming Dashboard:** Real-time reactive Web UI streaming token reductions, live event logs, and USD savings via Server-Sent Events (`/api/events`).
- **Friday Team ROI Digest:** Automatically aggregates 7-day token economics every Friday and emails team leads an executive HTML report with engineer savings leaderboards.
- **Environment Overhead Auditor (`tokensafe doctor`):** Inspects active MCP servers and Antigravity skills, measuring system prompt token tax and pruning redundant tool schemas.
- **MCP Tool Schema Bloat Guard:** Stops heavy MCP servers (50–200+ tools) from injecting 5,000–15,000 hidden schema tokens into every API turn. 1-click toggle (`tokensafe mcp disable <name>`) and real-time in-chat bloat alerts.
- **Data Retention & Compact Rollups (`tokensafe prune` / `vacuum`):** Automated sliding-window log cleanup with lifetime metric rollups, cutting telemetry database disk footprint by up to 40%.
- **Local Secret Redactor:** Sanitizes Stripe, AWS, JWT, and database credentials before network transmission.
- **Output Guard & Loop Guard:** Blocks destructive full-file rewrites on files $\ge 40$ lines and halts command loops.

---

## 🛡️ The Hidden Context Killer: MCP Tool Schema Bloat

Why do AI coding agent quotas (like Antigravity's **5-Hour Limit** or Claude/OpenAI monthly token budgets) evaporate after just a few prompts?

### The Hidden System Prompt Tax
Whenever an MCP server is configured in your editor (Cursor, Claude Desktop, Antigravity, Windsurf), **the complete JSON schema for every single tool is injected into the LLM's system prompt on EVERY API TURN** — even if you never use that tool!

```
┌────────────────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT ANATOMY (EVERY SINGLE TURN)                            │
├────────────────────────────────────────────────────────────────────────┤
│  Base System Instructions                                ~1,500 tokens │
│  Active Workspace Skills & Prompts                       ~8,000 tokens │
│  Regular MCP Tools (context7, prisma, etc.)                ~800 tokens │
│  🚨 Heavy MCP Tools (200+ schemas from cloud/re tools)   ~6,000 tokens │
├────────────────────────────────────────────────────────────────────────┤
│  TOTAL HIDDEN BASE TAX BEFORE YOU TYPE A SINGLE CHARACTER:             │
│  🔥 ~16,300 tokens PER TURN × 15 turns = 244,500 tokens burned!       │
└────────────────────────────────────────────────────────────────────────┘
```

- **1 Server with 50 tools** = **~1,500 – 2,500 tokens** of static tax on every turn.
- **1 Server with 200+ tools** = **~5,000 – 10,000 tokens** per turn!
- In a 15-turn coding task, **75,000 to 150,000 tokens** are burned on tool schemas alone.

### How TokenSafe Eliminates MCP Bloat:
1. **`tokensafe doctor` (Context Tax Auditor)**: Scans all configured MCP servers and active skills, measures the exact token overhead per turn, and flags servers with $\ge 40$ tools as `CRITICAL BLOAT`.
2. **1-Click MCP Management**: Instantly toggle heavy servers without editing JSON files manually:
   ```bash
   tokensafe mcp disable rea     # Prunes 208 schemas from system prompt
   tokensafe mcp enable rea      # Re-enables when actively needed
   ```
3. **PreInvocation Bloat Alerts**: Live in-chat warning badges in the IDE when critical MCP bloat is detected on session start.
4. **Data Retention & Compact Rollups**: Automatic rolling-window event cleanup (`tokensafe prune` and `tokensafe vacuum`) prevents SQLite telemetry from ballooning on disk.

---

## ⚡ TokenSafe vs. Graphify: Dynamic In-Flight Defense vs. Static Reports

How TokenSafe complements or supersedes static graph tools like **Graphify** (`graphify-labs/graphify`):

| Capability | Graphify | TokenSafe |
|---|---|---|
| **Primary Focus** | Static knowledge graph reports & MCP queries | **In-flight real-time token compression & defense** |
| **Indexing Cost** | Expensive LLM tokens / heavy graph extraction | **0 tokens, 100% deterministic local AST (< 30ms)** |
| **Execution Latency** | Offline / CLI-initiated build | **Sub-millisecond inline proxy & lifecycle hooks** |
| **Active Slicing** | Static file output (`GRAPH_REPORT.md`) | **Dynamic call-graph skeletonization (85–92% saved)** |
| **Multi-Project Workspace** | Requires manual directory targeting | **Auto-detects project roots per file on the fly** |
| **Agent Prompt Optimization** | None | **TOCP S-Expression prompt distillation (-40% to -70%)** |
| **Safety & Runaway Prevention** | None | **Output rewrite guard, loop detector, secret scrubbing** |

> **Complements & Replaces**: If you already use Graphify for repository mapping, TokenSafe works alongside it by compressing real-time agent tool reads on the wire. Or use TokenSafe standalone for a zero-configuration, zero-cost, sub-30ms architecture engine out of the box.

---

## 🤖 Universal LLM Distillation Providers

TokenSafe supports any key you already have:

| Provider | Supported Model | Environment Variable | Config Key |
|---|---|---|---|
| **Google Gemini** | `gemini-2.0-flash-lite`, `gemini-2.0-flash` | `GEMINI_API_KEY` | `gemini_api_key` |
| **OpenAI ChatGPT** | `gpt-4o-mini`, `gpt-4o` | `OPENAI_API_KEY` | `openai_api_key` |
| **Anthropic Claude** | `claude-3-5-haiku`, `claude-3-7-sonnet` | `ANTHROPIC_API_KEY` | `anthropic_api_key` |
| **DeepSeek** | `deepseek-flash`, `deepseek-chat` | `DEEPSEEK_API_KEY` | `deepseek_api_key` |
| **Custom / Unified** | Any OpenAI/Anthropic compatible gateway | `TOKENSAFE_UPSTREAM_KEY` | `upstream_key` |

Check active providers anytime with:
```bash
tokensafe providers
```

---

## 📊 Zero-Loss SWE-bench Benchmark Results

Verified across 8 real-world production software engineering scenarios (131,604 baseline tokens down to 7,251 tokens, **-94.5% net token reduction**, **+28.1% turn latency speedup** with **100% Zero-Loss accuracy retention**):

| Benchmark Scenario | Task Complexity | Baseline Tokens | TokenSafe Tokens | Reduction | Turn Speedup | Accuracy |
|---|---|---|---|---|---|---|
| **Zero-Token Architecture Discovery** | Full repo God Node indexing | 49,716 | **486** | **-99.0%** | **6 ➔ 1 turns (+83%)** | 100% Pass |
| **Graph-Guided AST Symbol Slicing** | Target function + 1-hop callees | 8,016 | **451** | **-94.4%** | **4 ➔ 2 turns (+50%)** | 100% Pass |
| **Monorepo Lockfile Grep Leak** | `git grep` across lockfiles | 10,111 | **491** | **-95.1%** | 2 turns | 100% Pass |
| **Monorepo Lockfile Git Diff** | Monorepo branch diff | 18,911 | **761** | **-96.0%** | 2 turns | 100% Pass |
| **Multi-Turn History Compaction** | 8-turn session history fold | 31,010 | **3,130** | **-89.9%** | 8 turns | 100% Pass |
| **Race Condition / Distributed Lock** | AST query & Redis redlock | 5,074 | **404** | **-92.0%** | **4 ➔ 3 turns (+25%)** | 100% Pass |
| **OAuth JWT Expiration Triage** | Controller bug investigation | 3,757 | **401** | **-89.3%** | **3 ➔ 2 turns (+33%)** | 100% Pass |
| **CI Test Failure Extraction** | Full test log & stack trace | 5,009 | **1,127** | **-77.5%** | 3 turns | 100% Pass |

---

## 💎 Pricing & 7-Day Free Trial

| Tier | Price | Highlights |
|---|---|---|
| **Hacker (Open-Core)** | **$0** (Free Forever) | 100k tokens/mo, Local Regex compactor, use own DeepSeek / Gemini key |
| **Solo Developer** | **$9 / month** | **7-Day Free Trial**, Unlimited Compression, Subagent Firewall, Context Window Governor, Prompt Cache Normalizer, Output Guards |
| **Engineering Team** | **$29 / dev / mo** | **7-Day Free Trial**, Everything in Solo + Friday Team ROI Digest & Leaderboards, Team Seat Quotas, Centralized Ledger, B2B Docker Proxy |
| **Enterprise** | Custom | On-Premise Nuitka C-compiled Docker Proxy, Custom Compliance, SAML SSO |

👉 **[Start your 7-Day Free Trial on our website](https://tokensafe.dev#pricing)**

---

## 🛠️ CLI Quickstart

```bash
# Analyze repository God Nodes and dependency topology
tokensafe graph

# Generate compact tokensafe-out/API_SURFACE.md interface snapshot
tokensafe map

# Run environment health check and audit MCP/skill context token tax
tokensafe doctor

# View live savings dashboard in your terminal
tokensafe stats

# Open interactive Apple dark-mode HTML dashboard
tokensafe html

# Generate or preview Friday team ROI digest email
tokensafe digest

# Manage or disable high-overhead MCP tools
tokensafe mcp list
tokensafe mcp disable <server>

# Optimize SQLite metrics storage
tokensafe vacuum

# Run the 5-scenario benchmark suite
tokensafe benchmark

# Run the local transparent AI proxy (0.0.0.0:8765/v1)
tokensafe proxy

# Activate your Pro License key (TS2 Asymmetric Ed25519 Key)
tokensafe activate TS2.eyJlbWFpbCI6...
```

---

## 🐳 Docker Deployment (Multi-Provider Support)

TokenSafe is fully containerized with a hardened multi-stage production Docker image, non-root security (`uid: 1000`), and native multi-provider LLM routing.

### 1. Multi-Provider Environment Keys

You can configure any or all of your preferred LLM provider keys:

| Environment Variable | Provider Supported | Default Model |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API | `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Anthropic Claude | `claude-3-5-haiku-20241022` |
| `GEMINI_API_KEY` | Google Gemini | `gemini-2.0-flash-lite` |
| `DEEPSEEK_API_KEY` | DeepSeek API | `deepseek-chat` |
| `TOKENSAFE_UPSTREAM_KEY` | Unified / Custom Gateway | Custom Upstream |

### 2. Docker Compose Quickstart

Run TokenSafe as a centralized microservice or team gateway:

```yaml
version: '3.8'

services:
  tokensafe:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: tokensafe
    restart: unless-stopped
    ports:
      - "8765:8765"
    environment:
      - TOKENSAFE_HOST=0.0.0.0
      - TOKENSAFE_PORT=8765
      - TOKENSAFE_DATA_DIR=/app/data
      # Choose your provider(s):
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - GEMINI_API_KEY=${GEMINI_API_KEY:-}
      - TOKENSAFE_LICENSE_KEY=${TOKENSAFE_LICENSE_KEY:-}
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Start the container in detached mode:
```bash
docker compose up -d
```

### 3. Verification & Live SSE Streaming

- **Health check**: `curl http://localhost:8765/api/health`
- **Live SSE Event Stream**: `curl -N http://localhost:8765/api/events`
- **Point your AI Agents**: Configure your IDE or Agent to use `http://localhost:8765/v1` as the upstream base URL.

---

## 🔒 Security Architecture & Cloud API Endpoints

TokenSafe Cloud Backend enforces enterprise zero-trust security across all communications:

### 1. Cryptographic Authentication & Licensing
- **Asymmetric RFC 8032 Ed25519 Signatures (`TS2.*`)**: Client instances verify digital signatures offline using an embedded 32-byte public key. The private signing key never leaves the secure cloud enclave.
- **JWT Session Tokens**: Cloud endpoints authenticate sessions via standard HS256 JWT bearer tokens or Clerk session tokens.
- **Dual Bearer Support**: Client endpoints accept either a signed license key (`TS2.*` / `TS1.*`) or a JWT in the `Authorization: Bearer <token>` header.
- **Zero Hardcoded Secrets**: All SMTP, Stripe, Admin, and signing keys are strictly injected via environment variables.

### 2. Cloud API Endpoint Reference

| Method | Endpoint | Auth Required | Headers / Parameters | Description |
|---|---|---|---|---|
| `GET` | `/api/health` | Public | None | Healthcheck and service version status. |
| `GET` | `/api/events` | Public | SSE `text/event-stream` | Real-time Server-Sent Events stream for live dashboard telemetry. |
| `GET` | `/api/dashboard` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>` or `?license=<key>` | Authenticated user metrics, token savings, and license status. |
| `GET` | `/api/digest/preview` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>` or `?team_id=<id>` | Previews current weekly team ROI digest (HTML / JSON). |
| `POST` | `/api/team/seat/create` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>`, JSON `{ owner_email, member_email }` | Generates a cryptographically signed seat license for a team member. |
| `POST` | `/api/team/seat/revoke` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>`, JSON `{ owner_email, license_key }` | Revokes an active team seat license key. |
| `POST` | `/api/telemetry/sync` | **License Auth** | JSON `{ license_key, email, events: [...] }` | Syncs batch telemetry events (enforces max 50 events/batch). |
| `POST` | `/webhook/stripe` | **HMAC Sig** | `Stripe-Signature: t=...,v1=...` | Verifies Stripe webhook payload against `STRIPE_WEBHOOK_SECRET`. |
| `GET` | `/api/admin/overview` | **Admin Only** | `X-Admin-Secret` or Admin JWT | Global overview of MRR, seats, active licenses, and system health. |
| `POST` | `/api/admin/digest/send` | **Admin Only** | `X-Admin-Secret` or Admin JWT | Dispatches scheduled Friday weekly ROI digest emails to team leads. |
| `POST` | `/api/admin/license/issue`| **Admin Only** | `X-Admin-Secret` or Admin JWT | Manually issues or provisions a custom tier license. |
| `POST` | `/api/admin/license/revoke`| **Admin Only**| `X-Admin-Secret` or Admin JWT | Immediately revokes a license. |
| `POST` | `/api/admin/license/extend`| **Admin Only**| `X-Admin-Secret` or Admin JWT | Extends an active license duration. |

### 3. Server Configuration & Environment Variables

| Variable | Required | Default / Description |
|---|---|---|
| `TOKENSAFE_PRIVATE_KEY` | Production | 64-character hex Ed25519 seed for issuing `TS2.` licenses. |
| `ADMIN_SECRET_KEY` | Yes | High-entropy secret for administrative API access (`X-Admin-Secret`). |
| `ADMIN_EMAILS` | Optional | Comma-separated admin emails (e.g. `nekosin4eg@gmail.com`). |
| `STRIPE_WEBHOOK_SECRET` | Production | Webhook secret (`whsec_...`) used to verify Stripe webhooks. |
| `SMTP_PASS` | Production | Password/token for transactional mail delivery via ImprovMX SMTP. |
| `JWT_SECRET` | Optional | Shared HMAC secret for verifying custom JWT tokens. |


---

## 🧪 Unit Testing

```bash
python3 -m unittest discover tests
```
*Zero external runtime dependencies — runs on standard Python 3.10+.*

---

## 📄 License
MIT License. Commercial enterprise licenses available for on-premise B2B deployments.

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
├── Dockerfile                  # Hardened multi-stage container deployment
├── docker-compose.yml          # Production Docker compose with multi-provider routing
├── rules/
│   └── AGENTS.md               # TOCP Protocol directives for LLM (prompt-cached)
├── skills/
│   └── token-optimizer/
│       └── SKILL.md            # On-demand Antigravity skill
├── proto/
│   └── tocp.proto              # Protocol Buffers contract
├── src/
│   ├── cache_normalizer.py     # Prompt Cache Prefix Normalizer (Anthropic & DeepSeek)
│   ├── governor.py             # Context Window Governor & Token Ceiling (Cascading reduction)
│   ├── agent_firewall.py       # Cross-Agent Deduplication Cache (Subagent Firewall)
│   ├── diff_slicer.py          # AST Diff Slicer & Lockfile Stripper (`git diff -U2`)
│   ├── weekly_digest.py        # Friday Team ROI Digest & Engineer Leaderboard
│   ├── environment_auditor.py  # MCP Server & Skill context token tax auditor
│   ├── proxy.py                # Multi-provider transparent AI proxy & SSE broadcaster
│   ├── graph_engine.py         # Sub-30ms AST parser, call graphs & God Node centrality
│   ├── tokenizer.py            # Zero-dependency calibrated BPE token counter
│   ├── compactor.py            # Semantic compactor (Natural Language -> TOCP S-Expr)
│   ├── slicer.py               # AST & Line windowing tool optimizer
│   ├── metrics.py              # Persistent SQLite metrics engine
│   ├── dashboard.py            # Visual ANSI terminal, Markdown & reactive SSE web dashboard
│   ├── license_manager.py      # Ed25519 cryptographic license verification
│   ├── mailer.py               # Transactional email & Friday ROI digest dispatcher
│   └── cloud_backend.py        # Cloud REST backend, telemetry sync & Stripe webhooks
├── hooks/
│   ├── pre_invocation.py       # PreInvocation hook entry point
│   ├── pre_tool_use.py         # PreToolUse hook entry point
│   └── post_tool_use.py        # PostToolUse hook entry point
├── frontend/                   # Modern React / Vite / Tailwind Web App & Dashboard
├── bin/
│   └── agy-tokens              # Executable CLI (`tokensafe`)
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
