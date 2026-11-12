# Architecture Reference

How TokenSafe works under the hood: the processing pipeline, the polyglot AST engine, the context tax math, and the caching/distillation stack. Everything here runs locally unless explicitly marked as cloud.

## 1. Processing Pipeline

Every request from a coding agent passes through the same local pipeline in milliseconds:

1. **Guard** (`src/guard.py`): the tool call (shell command or file write) is inspected before execution. Unsafe operations are blocked with an explicit reason.
2. **Compactor** (`src/compactor.py`): verbose prompts are distilled into compact intent expressions (labeled *estimated* in [METRICS.md](../METRICS.md)).
3. **Slicer** (`src/slicer.py`, `src/graph_engine.py`): file reads return only the target symbol plus 1-hop dependencies instead of whole files.
4. **Sanitizer** (`src/command_sanitizer.py`): terminal output is stripped of lockfiles and stack noise down to the failing assertion.
5. **Metrics** (`src/metrics.py`): every savings event is recorded locally to SQLite as `measured` or `estimated`.

## 2. Polyglot AST Engine (10+ Languages)

The deterministic local AST state machine folds non-target function bodies in **< 30ms** with zero external runtime dependencies, preserving target symbols and 1-hop dependencies with full executable bodies.

| Language / Framework | File Extensions | Preserved Structures | Folding Mechanism |
|---|---|---|---|
| **TypeScript / React** | `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` | React components (`React.FC`, `memo`, `forwardRef`, multi-line destructuring), arrow functions, hooks, classes, interfaces | Target & 1-hop bodies kept full; non-target bodies folded into `{ /* ... */ };` |
| **Python** | `.py` | Classes, typed signatures, docstrings, decorators, `async def` | Native `ast.parse` tree pruning with `...` body replacement |
| **Go** | `.go` | Struct declarations, interfaces, methods `func (r *Recv) Method(...)`, generics `func Map[T any]` | Non-target functions folded; struct layouts and `select` blocks preserved |
| **Rust** | `.rs` | `struct`, `enum`, `impl` blocks, `trait` blocks, `pub(crate) async fn`, `const fn` | `impl` containers stay open; method bodies folded into `{ /* ... */ }` |
| **Java & C#** | `.java`, `.cs` | Classes, records, methods with access modifiers, generic return types (`Task<T>`, `List<E>`), `throws` | Method bodies folded; classes and interfaces remain structural |
| **C & C++** | `.c`, `.cpp`, `.cc`, `.cxx`, `.h`, `.hpp` | Classes, structs, namespaces, scoped methods `ClassName::method`, `const`, `noexcept`, `override`, destructors | Functions folded; namespace and class hierarchies preserved |
| **PHP, Kotlin, Swift** | `.php`, `.kt`, `.kts`, `.swift` | Classes, `function`, `fun`, `func` declarations with visibility modifiers | Function bodies folded into `{ /* ... */ }` |
| **CSS, SCSS, LESS, Tailwind** | `.css`, `.scss`, `.sass`, `.less` | **Design tokens (`:root`, `@theme`, CSS variables, `$var`) preserved in full**, container queries (`@media`, `@supports`, `@container`, `@layer`) kept open | Component selectors, `@keyframes`, and mixins folded unless matching `target_symbol` |

The separate **Graph Engine** (`src/graph_engine.py`) indexes `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go` for architecture-wide queries (God Nodes, API surface maps). The Skeletonizer covers the full matrix above; the Graph Engine covers the subset.

### Engine Guarantees

- **Design token preservation**: skeletonizing stylesheets keeps `:root { ... }`, Tailwind v4 `@theme { ... }`, CSS variables `--*`, and SCSS `$vars` intact, so agents never invent colors, radii, or font rules.
- **Strict arrow function parser**: arrow functions require `=>` before any `{` counts as a function body. Destructuring parameters and parenthesized ternaries are left intact.
- **Directory tree optimization** (`tokensafe:list_dir`): replaces verbose `ls -la` output with a compact, depth-bounded tree without permissions, timestamps, or lockfiles.
- **Graph-guided neighborhood slicing**: `tokensafe:smart_read_file(path, symbol="Target")` preserves the target body **and its 1-hop callees/callers** while folding unrelated code.
- **Comment & string bracket immunity**: multi-line block comments, JSX `{/* ... */}`, template strings, and escaped quotes are tracked by `BraceLexer` so brackets never desynchronize.
- **Control-flow immunity**: `if (...)`, `catch (err) {`, `for (...)`, and `while (...)` are protected from folding.

## 3. The Hidden Context Tax

Whenever an MCP server is enabled in an editor, **the full JSON schema for every tool is injected into the system prompt on every turn**, even when unused:

```text
┌────────────────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT ANATOMY (EVERY SINGLE TURN)                            │
├────────────────────────────────────────────────────────────────────────┤
│  Base System Instructions                                ~1,500 tokens │
│  Active Workspace Skills & Prompts                       ~8,000 tokens │
│  Regular MCP Tools (context7, prisma, etc.)                ~800 tokens │
│  [!] Heavy MCP Tools (200+ schemas from large servers)   ~6,000 tokens │
├────────────────────────────────────────────────────────────────────────┤
│  TOTAL HIDDEN BASE TAX BEFORE YOUR FIRST LINE OF CODE:                 │
│  ~16,300 tokens PER TURN x 15 turns = 244,500 tokens burned            │
└────────────────────────────────────────────────────────────────────────┘
```

- **1 server with 50 tools** = **~1,500 to 2,500 tokens** of static tax per turn.
- **1 server with 200+ tools** = **~5,000 to 10,000 tokens** per turn.
- Across a 15-turn coding session, **75,000 to 150,000 tokens** go solely to tool schemas.

### How TokenSafe Cuts MCP Overhead

1. **`tokensafe doctor` (Environment Auditor)**: scans configured MCP servers and skills, measures token overhead per turn, and flags servers with 40+ tools as `CRITICAL BLOAT`.
2. **1-click MCP toggle**: enable or disable heavy servers without manual JSON edits:
   ```bash
   tokensafe mcp disable rea     # prunes heavy schemas from the system prompt
   tokensafe mcp enable rea      # re-enables when needed
   ```
3. **Session start bloat alerts**: surfaces warning badges in chat when heavy MCP bloat is active.
4. **Telemetry retention** (`tokensafe prune` / `vacuum`): compacts SQLite metrics with rolling-window rollups, reducing the local database footprint by up to 40%.

## 4. Context Budget HUD, Governor & Epoch Architecture

Teams usually notice context exhaustion only when code quality drops mid-task. TokenSafe exposes live window usage as a native MCP tool:

```json
{ "tool": "tokensafe:context_budget", "args": { "model": "claude-x" } }
```

```json
{
  "window_tokens": 200000,
  "used_estimated": 168000,
  "used_pct": 84.0,
  "breakdown": { "tools": 165000, "history": 2300, "prompts": 700, "other": 0 },
  "threshold": "warning",
  "recommendation": "Context is heavy. Prefer tokensafe:smart_read_file ... and consider tokensafe:compact_conversation."
}
```

- **Window resolution**: explicit argument, then `TOKENSAFE_CONTEXT_WINDOW`, then model-family default (Gemini 1M, Claude 200k, GPT 128k).
- **80% session warning**: injected once per session by the `pre_invocation` hook when context usage approaches the threshold.
- **Append-Only Epoch Governor & multi-breakpoint prefix normalizer**: in proxy mode, `ContextGovernor` never mutates historical turns (`messages[0..N-2]`) inside an epoch, preserving LLM provider KV-cache hits until context reaches **80% of the model's window**, when an atomic `EPOCH_ROLLOVER` consolidates middle turns into an Epoch Ledger. `PromptCacheNormalizer` runs *after* governance, sorts `tools` deterministically, strips volatile IDs/hit counters, and injects multi-breakpoint Anthropic `cache_control` (tools, static system, penultimate turn) or OpenAI `prompt_cache_key`.
- **Honest accounting**: counts only TokenSafe-instrumented flows and labels them `estimated` (a floor, never a fabricated total; see [METRICS.md](../METRICS.md)).

## 5. Diff-Aware Read-Cache (`SUBAGENT_DEDUP`)

Prevents subagent swarms from reading identical files repeatedly. Entries are keyed by **SHA-256 of raw file bytes**, so they survive MCP server restarts and `git checkout` while invalidating immediately on any real edit (bounded at 256k chars per artifact and 500 recent entries).

## 6. Prompt Distillation & Three-Tier Cache

TokenSafe distills verbose prompts into compact intent expressions (`-40% to -70%` tokens).

### Deployment Modes

| Deployment Mode | Supported Tier | Distillation Engine | Required API Keys | Setup |
|---|---|---|---|---|
| **TokenSafe Cloud (SaaS)** | **Engineering Team & Solo** | **Google Gemini Flash-Lite** (`gemini-flash-lite-latest`) | **None (zero keys needed)** | Included in subscription |
| **Local Regex Compactor** | **Developer Sandbox (Free)** | **Deterministic multilingual regex** (9 languages) | **None** | Local execution only (500,000 tokens/mo) |
| **Self-Hosted Docker** | **Enterprise & VPC** | **BYOK multi-provider** (Gemini, DeepSeek, OpenAI, Claude) | `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, etc. | Private VPC container deployment |

### Token Arbitrage Guard

To keep distillation overhead lower than the tokens it saves:

1. **Smart gating (`llm_distill_min_tokens: 100`)**: prompts under 100 tokens stay strictly local via regex heuristics (0 LLM tokens, <1ms).
2. **Output token ceiling (`distill_max_tokens: 60`)**: caps compiled output at 60 tokens to prevent compiler arbitrage loss.
3. **Three-tier cache**:
   - **L1 (in-memory LRU)**: sub-millisecond hits inside active sessions.
   - **L2 (persistent SQLite)**: stores normalized SHA-256 prompt hashes in `~/.tokensafe/data/metrics.db` across restarts so repeated workflows (`git commit`, `run tests`, `fix lint`) cost 0 tokens. The directory is shared by all agents on the machine (override with `TOKENSAFE_DATA_DIR`; Docker uses `/app/data`).
   - **L3 (shared team cache)**: *Team tier*. Syncs distilled intents and AST skeletons across teammates and CI runners (`/api/team/cache`) for warm 0ms team cache hits.

Inspect your active license tier, distillation mode, and cache stats anytime: `tokensafe providers`.

## 7. Self-Hosted Docker Deployment

The repository ships a hardened multi-stage `Dockerfile` (non-root `uid: 1000`) and a `docker-compose.yml` for containerized runs. The local transparent proxy is available directly via `tokensafe proxy` (`0.0.0.0:8765/v1`).

Supported, production-grade self-hosting (private VPC, BYOK provider keys, zero-telemetry / air-gapped mode, SAML/OIDC SSO) is part of the **Enterprise & VPC** plan and is deployed together with the TokenSafe engineering team: [corp@tokensafe.dev](mailto:corp@tokensafe.dev).
