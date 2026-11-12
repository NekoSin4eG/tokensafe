<p align="center">
  <img src="assets/logo.svg" alt="TokenSafe Logo" width="88" height="88" />
</p>

<h1 align="center">TokenSafe</h1>
<!-- mcp-name: dev.tokensafe/tokensafe -->

<p align="center">
  <b>Local-First Context Control, Prompt Caching &amp; Secret Guardrails for Coding Agents</b><br>
  Reduce your AI bills by up to 99%. Let your team ship clean code faster and cheaper.<br>
  Your coding agents read only the code each task needs and keep prompt caches intact across turns. Everything runs locally on your machine, so your team fixes fewer broken edits and keeps secrets out of git.<br>
  Works with <b>Cursor, Claude Code, Codex, Windsurf, Google Antigravity, Gemini CLI, and ChatGPT</b>.
</p>

<p align="center">
  <a href="https://github.com/tokensafe-dev/tokensafe/actions/workflows/test.yml"><img src="https://img.shields.io/github/actions/workflow/status/tokensafe-dev/tokensafe/test.yml?branch=main&label=tests&style=flat-square&color=10B981" alt="Tests"></a>
  <a href="https://www.npmjs.com/package/tokensafe"><img src="https://img.shields.io/npm/v/tokensafe.svg?color=38BDF8&style=flat-square" alt="npm"></a>
  <a href="https://www.npmjs.com/package/tokensafe"><img src="https://img.shields.io/npm/dw/tokensafe.svg?color=10B981&label=npm%20downloads&style=flat-square" alt="npm downloads"></a>
  <a href="https://pypi.org/project/tokensafe/"><img src="https://img.shields.io/pypi/v/tokensafe.svg?color=38BDF8&style=flat-square" alt="PyPI"></a>
  <a href="https://github.com/tokensafe-dev/tokensafe/stargazers"><img src="https://img.shields.io/github/stars/tokensafe-dev/tokensafe?style=flat-square&color=334155" alt="Stars"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-0F766E?style=flat-square" alt="License"></a>
  <a href="https://tokensafe.dev#pricing"><img src="https://img.shields.io/badge/Team_Trial-7_Days_Free-10B981?style=flat-square" alt="Trial"></a>
</p>

<p align="center">
  <a href="#install--agents">Install</a> &bull;
  <a href="#cli-quickstart">Quickstart</a> &bull;
  <a href="#why-tokensafe">Why TokenSafe</a> &bull;
  <a href="#benchmarks">Benchmarks</a> &bull;
  <a href="#pricing-and-7-day-team-trial">Pricing</a> &bull;
  <a href="#faq">FAQ</a> &bull;
  <a href="#reference">Reference</a> &bull;
  <a href="https://tokensafe.dev">Website &amp; Docs</a>
</p>

---

## Install & Agents

Connect TokenSafe to the AI coding agents on your workstation in one command:

```bash
# Option 1: Zero-Install via npx (recommended)
npx tokensafe

# Option 2: Python / PyPI
pip install tokensafe && tokensafe

# Option 3: Direct shell script (macOS / Linux)
curl -fsSL https://tokensafe.dev/install.sh | bash
```

The installer detects which agents live on your machine, registers the TokenSafe MCP server, injects project rules, and wires lifecycle hooks where the agent supports them.

| Agent | Integration |
|---|---|
| **Google Antigravity** | Plugin symlink + hooks + skills + MCP (loaded automatically on startup) |
| **Claude Code CLI** | MCP + `SessionStart` / `UserPromptSubmit` / `PreToolUse` hooks |
| **Cursor** | MCP + `beforeShellExecution` / `beforeReadFile` hooks |
| **Claude Desktop** | MCP registration (`claude_desktop_config.json`) |
| **Windsurf** | MCP registration + global rules |
| **OpenAI Codex** | MCP in `~/.codex/config.toml` (stdio + cloud HTTP natively) |
| **Cline / Roo Code** | MCP registration (VS Code) |
| **GitHub Copilot** | MCP registration |
| **Z.ai Code** | MCP registration |
| **ChatGPT** | Cloud connector, nothing to install |
| **Gemini CLI** | MCP registration |

Claude Desktop / Cursor MCP config:

```json
{
  "mcpServers": {
    "tokensafe": { "command": "npx", "args": ["-y", "tokensafe", "mcp"] }
  }
}
```

Cloud features (team metrics, managed distillation, shared team cache, billing) run on the remote endpoint `https://api.tokensafe.dev/mcp` with `Authorization: Bearer <TS2. license key>`. Four public tools work without a key, so ChatGPT connects with authentication set to **None**.

Full per-agent configuration, hook details, and the Codex/ChatGPT setup live in [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md).

## CLI Quickstart

```bash
# View live savings dashboard in your terminal
tokensafe stats
```

```text
⚡ TOKENSAFE CONTEXT OPTIMIZER DASHBOARD
Total Tokens Saved:   4,524,156 tokens
Efficiency Gain:      [████████████████████████░░] 92.5%
Context Traffic:      4,889,255 raw → 370,574 optimized (2,132 events)

• Command Sanitizer (CLI)        :   727,300 tokens saved (263 events)
• Architecture Graph             :   758,256 tokens saved (13 events)
• Multi-turn History Fold        : 2,897,897 tokens saved (471 events)
• Prompt Compactor (local+cache) :    43,502 tokens saved (1,255 events)
```

```bash
# Audit your environment: MCP tool overhead, skills tax, risky settings
tokensafe doctor

# Recursive security & hygiene audit of a repository
tokensafe guard audit

# Preview prompt compaction on custom input
tokensafe test "fix the validate_token method in src/auth.py to check jwt expiration"

# Map architectural hotspots before multi-file changes
tokensafe graph

# Disable a heavy MCP server without editing JSON
tokensafe mcp disable <server>

# Compile and preview the Friday team ROI digest
tokensafe digest

# Activate a Team or Solo license key
tokensafe activate TS2.eyJlbWFpbCI6...
```

The full command reference (24 commands) is in [Reference](#reference).

## Why TokenSafe

Giving coding agents raw access to entire repositories creates three daily costs:

1. **Senior hours burned on AI PRs**: reading 2,000-line modules for a 10-line fix makes models forget imports, invent methods, and regress adjacent code.
2. **Secret leaks and unsafe shortcuts**: static prompt rules in `.cursorrules` wear off mid-session. Agents hardcode fallback credentials, disable TLS checks, or stage `.env` files with `git add .`.
3. **Mid-sprint quota stops and blind spend**: bloated diffs, lockfiles, and verbose MCP schemas burn 5-hour limits while engineering leads lack per-seat ROI visibility.

| Outcome | What changes for your team | How it is enforced |
|---|---|---|
| **Clean code on the first pass** | Agents read only target functions, design tokens, and 1-hop dependencies instead of 2,000 lines of noise. | Sub-30ms local AST skeletonizer, architecture graph, predictive signature prefetch, lockfile-stripping diff slicer, loop guard. |
| **Zero leaked secrets in git** | Hardcoded password fallbacks, TLS bypasses, raw SQL interpolation, and `.env` commits are stopped before reaching disk or staging. | Deterministic TokenSafe Guard on every tool call, output guard on large writes, local PII/key redaction. |
| **Predictable sprint velocity & ROI** | Teammates get warm cache hits, and CTOs receive a Friday ROI digest separating `measured` and `estimated` savings. | Shared team cache, live context budget HUD, per-seat dashboard. |

A $29 seat pays for itself by saving one hour of senior code review or preventing one leaked key. Typical payback: **1 to 3 days** of team usage.

## Before and After

One contrast: an agent asked to change one helper in a 1,420-line file receives 94 lines of target logic and type contracts instead of the whole module. The same principle applies to terminal output, diffs, and session history.

| What breaks coding agents | How TokenSafe fixes it | The outcome |
| :--- | :--- | :--- |
| **"Lost in the Middle" context fade**: an agent reads a 2,000-line file to change one 10-line helper, forgets imports, and invents methods. | **AST skeletonizer** folds inert bodies into typed signatures while keeping the target symbol and 1-hop callees intact. | Focused signal: the model keeps full type contracts without drowning in unrelated logic. |
| **Terminal log flooding**: `npm test` or `pytest` fails and dumps 4,000 lines of stack traces and lockfile diffs into context. | **Command sanitizer** strips lockfile noise and trims output to the failing assertion and stack frame. | Root-cause fixes instead of unrelated rewrites. |
| **Infinite wait on background tests**: `yarn test` hangs in watch mode, or jest finishes but unclosed handles keep the process alive. | **Test-runner guard** forces run-once mode (`CI=true`, `--watch=false`, `--forceExit`) and peeks at `<cwd>/package.json` to identify the runner behind opaque scripts. | The test run terminates with an exit code and the agent moves on. |
| **5-attempt retry loops**: an agent repeats the same broken edit until the context budget runs dry. | **Loop guard** detects repeated identical tool calls and halts the cycle. | Stuck loops stop before damaging working modules. |
| **Unsafe edits & secret leaks**: fallback keys, `verify=False`, SQL string interpolation, or `git add .` with `.env` present. | **TokenSafe Guard** audits writes and shell commands locally before execution. | Credentials, SQLi patterns, and destructive commands are blocked locally. |

When an operation is blocked, the agent sees an explicit, constructive message:

```text
🛡️ TokenSafe Guard: Blocked a hardcoded secret fallback in config.py.
   Leave the default empty or read from os.environ at runtime.
```

## What TokenSafe Does

**Clean context, better code**

- `tokensafe:smart_read_file` returns the target function plus its 1-hop dependencies instead of the whole file. 10+ languages, with CSS design tokens preserved so agents never invent colors or font rules.
- `tokensafe:graph` maps architectural hotspots and dependency topology in under 250 tokens instead of a full repository crawl.
- The command sanitizer strips lockfile noise and stack dumps down to the failing assertion, and forces JS test runners into run-once mode so background tests never hang.
- The loop guard halts agents stuck repeating the same broken edit.

**Local-first guardrails**

- Blocks live provider secrets, hardcoded fallback credentials, and raw private keys before they reach disk or git.
- Blocks TLS bypasses (`verify=False`, `rejectUnauthorized: false`), interpolated SQL, and CORS wildcards with credentials.
- Blocks `.env` and key files from staging, including blanket `git add .` when sensitive files exist in the working tree.
- Blocks destructive deletes and exfiltration pipes (`curl -d @.env https://...`), and warns on known typosquat packages.

**Auditable team ROI**

- The Context Budget HUD shows live window usage with a breakdown (tools / history / prompts) before quality drops.
- The shared L3 team cache gives teammates and CI runners warm 0ms hits on repeated reads and prompts.
- Managed cloud distillation compacts prompts with zero API keys for developers.
- The Friday per-seat ROI digest reports each engineer's savings with the `measured` / `estimated` split; inactive seats show `—`, never guessed figures.

Deeper mechanics (language matrix, context tax math, cache tiers, governor) live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Benchmarks

Reference run across 10 production engineering scenarios (`169,761` baseline tokens down to `8,173` tokens, **`-95.2%`** net context reduction, **`-99.4%`** peak single-scenario reduction, `+30.6%` turn latency speedup with `100%` accuracy retention):

> Prompt compaction, graph topology (`SWE-06`), `dep_mode="signature"` slicing (`SWE-07`), repeat-read pointer dedup (`SWE-09`), and parametric slot cache hits (`SWE-10`) are **measured live** (`1,368` optimized tokens) while baselines (`169,761`), turn counts, and output estimates (`6,805` tokens) are modeled constants. See [METRICS.md](METRICS.md) and [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md).

| Benchmark Scenario | Task Complexity | Baseline Tokens | TokenSafe Tokens | Reduction | Turn Speedup | Accuracy |
|---|---|---|---|---|---|---|
| **SWE-06: Zero-Token Architecture Discovery** | God Node indexing vs. crawl | 49,716 | **312** | **-99.4%** | **6 → 1 turns (+83%)** | 100% Pass |
| **SWE-09: Repeat-Read Pointer Dedup** | `AgentFirewall` unchanged pointer | 17,388 | **208** | **-98.8%** | **2 → 1 turns (+50%)** | 100% Pass |
| **SWE-03: Monorepo Lockfile Git Diff** | Monorepo branch diff | 18,911 | **761** | **-96.0%** | 2 turns | 100% Pass |
| **SWE-07: Graph-Guided AST Symbol Slicing** | `dep_mode="signature"` + collapsed imports | 26,023 | **1,194** | **-95.4%** | **4 → 2 turns (+50%)** | 100% Pass |
| **SWE-10: Parametric Distillation Slot Cache** | Slot template hit (`0` LLM tokens) | 2,814 | **133** | **-95.3%** | **2 → 1 turns (+50%)** | 100% Pass |
| **SWE-08: Monorepo Lockfile Grep Leak** | `git grep` across lockfiles | 10,111 | **491** | **-95.1%** | 2 turns | 100% Pass |
| **SWE-01: Race Condition / Distributed Lock** | AST query & Redis redlock | 5,046 | **414** | **-91.8%** | **4 → 3 turns (+25%)** | 100% Pass |
| **SWE-05: Multi-Turn History Compaction** | 8-turn session history fold | 31,010 | **3,130** | **-89.9%** | 8 turns | 100% Pass |
| **SWE-02: OAuth JWT Expiration Triage** | Controller bug investigation | 3,733 | **403** | **-89.2%** | **3 → 2 turns (+33%)** | 100% Pass |
| **SWE-04: CI Test Failure Extraction** | Full test log & stack trace | 5,009 | **1,127** | **-77.5%** | 3 turns | 100% Pass |

### Why "Up to 99%" (Multiplicative Math & Honest Boundaries)

TokenSafe combines two multiplicative savings layers rather than relying on AST compression alone:

1. **Context Token Reduction (-85% to -99.4%):** Graph-guided `dep_mode="signature"` keeps only the edited symbol body intact while collapsing imports and 1-hop dependency implementations into signatures (`-95.4%` in `SWE-07`), Architecture Discovery maps repos in `< 350` tokens vs. full crawls (`-99.4%` in `SWE-06`), and repeat reads resolve to ~75-token integrity pointers (`-98.8%` in `SWE-09`, or `-99.56%` on raw file re-reads).
2. **KV-Cache Prefix Locking (`90.8%` hit rate at `0.10x` cached-read rate):** The Append-Only Epoch Governor (`ContextGovernor`) freezes history prefixes monotonically up to 80% of the model context window while `PromptCacheNormalizer` strips volatile metadata, sorts tool schemas, and injects provider `cache_control` breakpoints / `prompt_cache_key`. In a live 20-turn replay over `src/*.py` (`BENCHMARK_REPORT.md`):

| Multi-Turn 20-Turn Replay Metric | Value | Comparison |
|---|---|---|
| **Byte-Identical KV-Cache Prefix Hit Rate** | **`90.8%`** | `535,491 / 589,846` input tokens |
| **20-Turn Context Input Tokens** | **`-84.6%`** | `3,837,648` ➔ `589,846` |
| **Claude Sonnet 4.5 Input Bill (vs. Uncached)** | **`-96.8%`** | Cached input reads @ `$0.30/M` (`0.10x`) |
| **Claude Sonnet 4.5 Total Bill (vs. Uncached)** | **`-96.6%`** | `$11.6779` ➔ `$0.4005` (includes uncached `$15/M` outputs) |
| **Claude Sonnet 4.5 Total Bill (vs. Naive Mid-Trim Cache)** | **`-84.6%`** | `$2.5984` ➔ `$0.4005` |
| **DeepSeek V3 Total Bill (vs. Uncached)** | **`-96.9%`** | Cached reads `$0.028/M` (`0.10x`), outputs `$1.10/M` |

**Honest bounds:** When `~85–95%` fewer context tokens combine with a `90%+` KV-cache hit rate billed at `0.10x`, cached input spend approaches **1–2% of uncached cost (up to ~99% savings)** on graph-first, repeat-read, and cache-warm workflows. Total session invoices typically fall **80–97%** because newly generated output tokens and first-turn cache writes are never discounted by KV-caching.

> **How to read these numbers:** this benchmark is a **simulated** harness. Compression ratios and 20-turn KV-prefix byte matches are computed live from real files, while baselines and turn speedups are modeled. Production events in TokenSafe are explicitly labeled `measured` or `estimated` (see [METRICS.md](METRICS.md)).

## Metrics Honesty

TokenSafe labels every savings event as one of:

- **`measured`**: the model literally received fewer bytes than the tool produced (sliced file reads, filtered command output, AST skeletons).
- **`estimated`**: the model received a compact artifact and saved tokens are counted against a reasonable baseline (architecture graph vs. a full repo crawl; folded history digest injected while raw history stays in context).

Totals always display the split (`X metered · Y est.`). When a live metric is missing, the UI shows `—` rather than a placeholder number. USD figures are derived approximations (`tokens × $1.5 / 1M`), not provider invoices. Full contract: **[METRICS.md](METRICS.md)**.

## Pricing and 7-Day Team Trial

TokenSafe follows an open-core model: verify local context savings and guardrails on your own repo first, then roll out team caching, org policies, and per-seat ROI.

| Plan | Price | Monthly Allowance | Included Capabilities |
|---|---|---|---|
| **Developer Sandbox** | **$0** forever | **500,000 tokens / mo** | 9-language local regex compactor, local AST slicer CLI, local secret & `.env` guard, SQLite & terminal dashboard |
| **Engineering Team** *(Recommended)* | **$29 / seat / mo** *(~$24 / seat / mo billed $290/yr)* | **Unlimited** | **7-Day Free Trial**, **shared team cache (L3 monorepo hits)**, org-wide guard & git leak shield, Friday per-seat ROI digest, centralized seat provisioning & revoke, managed cloud distillation (zero keys needed) |
| **Enterprise & VPC** | **Custom** | **Unlimited** | Org audit log (guard forensics: who / what / when, CSV & SIEM export), central guard policies, self-hosted VPC proxy (private BYOK), zero-telemetry / air-gapped mode, SAML / OIDC SSO, dedicated onboarding & SLA |

*Working solo? [Individual Solo plans](https://tokensafe.dev#pricing) start at **$9 / mo** ($89 / yr).*

`7-Day Free Trial · Offline Ed25519 Seat Keys (TS2.*) · Zero Source Code Transmitted · Cancel anytime`

**[Start a 7-Day Team Trial](https://tokensafe.dev#pricing)** &nbsp;&nbsp; **[Run Free Locally](#install--agents)**

## FAQ

**Does my source code leave my machine?**
No. Slicing, guarding, compaction, and metrics run locally. The cloud receives only counters (token totals and event types), never file contents or prompts. Set `TOKENSAFE_TELEMETRY_DISABLED=1` to disable all telemetry sync; local features keep working.

**How does offline license verification work?**
License keys are `TS2.*` tokens signed with Ed25519. Your client verifies the signature offline against an embedded public key and caches the result for up to an hour. Enforcement never requires a network round trip.

**What do `measured` and `estimated` mean?**
`measured` means the model literally received fewer bytes than the tool produced. `estimated` means savings are counted against a reasonable modeled baseline. The dashboard always shows the split and never invents numbers for missing data.

**What exactly does the $29/seat plan include?**
Unlimited compaction and managed cloud distillation (zero API keys for developers), the shared L3 monorepo cache, org-wide guard policies, the Friday per-seat ROI digest, and centralized seat provisioning and revocation. Billed annually it is $290/seat/year (~$24/seat/mo).

**Can we self-host?**
The Enterprise & VPC plan covers a private VPC Docker proxy with BYOK provider keys, zero-telemetry / air-gapped mode, and SAML/OIDC SSO, deployed together with the TokenSafe engineering team: [corp@tokensafe.dev](mailto:corp@tokensafe.dev).

**What happens if a hook fails?**
All hook adapters fail open: an internal error never stalls your agent. Failures are logged locally so they can be reported and fixed, not silently swallowed.

## Reference

<details>
<summary><b>Local MCP tools (12)</b></summary>

| Tool | What it does |
|---|---|
| `get_architecture_graph` | Architectural hotspots and dependency topology before multi-file reading |
| `get_api_surface` | Compact markdown map of all exported interfaces, classes, and types |
| `smart_read_file` | AST-skeletonized file read preserving a target symbol and 1-hop dependencies |
| `scoped_search` | Token-bounded symbol and text search with line-numbered matches |
| `list_dir` | Clean directory tree without permissions, timestamps, or lockfiles |
| `slice_diff` | Unified diff slicing omitting lockfiles and minimizing context lines |
| `sanitize_command` | Per-stage shell command rewrites with bounded output |
| `guard_inspect` | Pre-flight security and hygiene audit for commands or code snippets |
| `context_budget` | Context window usage estimate with thresholds and recommendations |
| `compress_prompt` | Compiles a verbose developer prompt into a compact S-expression |
| `compact_conversation` | Folds a long agent transcript into a compact state ledger |
| `get_token_savings_metrics` | Cumulative token and cost savings analytics |
</details>

<details>
<summary><b>Cloud MCP tools (13)</b></summary>

Endpoint: `https://api.tokensafe.dev/mcp`, auth `Authorization: Bearer <TS1./TS2. key>`.

| Tool | Access | What it does |
|---|---|---|
| `get_public_stats` | Public | Aggregate product statistics |
| `check_latest_version` | Public | Latest published version and update banner |
| `get_setup_instructions` | Public | Copy-paste MCP snippets per agent |
| `search_docs` | Public | Search public documentation |
| `get_license_status` | Licensed | Active tier, seat, and trial state |
| `get_team_metrics` | Licensed | Team savings metrics with `measured` / `estimated` split |
| `distill_prompt` | Licensed | Managed cloud prompt distillation (zero API keys) |
| `team_cache_lookup` | Licensed | Shared L3 team cache lookup |
| `team_cache_store` | Licensed | Shared L3 team cache store |
| `list_invoices` | Licensed | Billing history |
| `generate_team_seat` | Licensed | Provision a signed `TS2.*` seat key |
| `revoke_team_seat` | Licensed | Revoke an active seat key |
| `preview_weekly_digest` | Licensed | Preview the Friday team ROI digest |
</details>

<details>
<summary><b>Full CLI reference (24 commands)</b></summary>

```text
tokensafe stats / markdown / html   Savings analytics (terminal / .md report / dashboard)
tokensafe benchmark                 Zero-context-loss benchmark suite (simulated harness)
tokensafe graph / map / skeleton    Architecture graph / API surface / AST skeleton of a file
tokensafe test "<prompt>"           Preview prompt compaction on custom input
tokensafe guard "<command>"         Pre-flight safety check for a shell command
tokensafe guard audit [path]        Recursive source audit for secrets & vulnerabilities
tokensafe doctor                    Audit MCP tool overhead and context token tax
tokensafe digest [--send]           Compile (and optionally dispatch) the weekly team digest
tokensafe mcp list|disable|enable   Manage configured MCP servers
tokensafe proxy [port]              Local transparent AI proxy (0.0.0.0:8765/v1)
tokensafe install / setup [--all]   Configure detected agent environments
tokensafe update / check-update     Self-update / check for a newer version
tokensafe activate <KEY>            Activate a Team or Solo license key
tokensafe license                   Show active tier and trial state
tokensafe providers                 List distillation providers and active mode
tokensafe sync                      Sync local telemetry with the cloud dashboard
tokensafe prune [--days N]          Compact metrics database (roll old events)
tokensafe vacuum                    Reclaim SQLite disk space
tokensafe tests [pytest]            Run the unit test suite (compact failure report)
tokensafe reset                     Reset all local token metrics
tokensafe --version                 Print version and exit
```
</details>

More documentation: [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/API.md](docs/API.md) · [METRICS.md](METRICS.md) · [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)

## Development and License

The client runs on Python 3.10+ with `cryptography>=42.0` as its sole runtime dependency for Ed25519 license checks; all AST slicing, SQLite metrics, MCP tooling, and guardrails run on the standard library ([details](docs/AGENT_SETUP.md#6-runtime-dependency-cryptography)).

```bash
# Run the unit test suite (compact failure report; full: --traceback / -v)
tokensafe tests
```

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy and vulnerability reporting: [SECURITY.md](SECURITY.md)
- Metrics and honesty contract: [METRICS.md](METRICS.md)

MIT License. Commercial licenses are available for self-hosted B2B and enterprise VPC deployments. Questions: [corp@tokensafe.dev](mailto:corp@tokensafe.dev) · [tokensafe.dev](https://tokensafe.dev)
