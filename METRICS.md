# TokenSafe Metrics Methodology

How we count "saved tokens" — and how we tell the honest numbers from the estimated ones.

## The two labels

Every optimization event carries a `measurement` label:

| Label | Meaning | Example |
|---|---|---|
| `measured` | **Literally metered I/O reduction.** The bytes the model actually received were smaller than what the tool would have returned. The delta is a fact. | Slicing a 1,000-line file read down to 200 lines; filtering a verbose command's output through the sanitizer. |
| `estimated` | **Counterfactual baseline.** The model received a compact artifact, and "saved" is measured against what a reasonable alternative behavior *would have* cost. | Receiving a 500-token architecture graph instead of crawling ~6.9M tokens of files. |

Both labels appear in every surface that aggregates savings:

- local `~/.tokensafe/data/metrics.db` (`events.measurement` column, exposed by `tokensafe stats`; shared across agents, `TOKENSAFE_DATA_DIR` overrides)
- the cloud dashboard (`metrics.saved_tokens_measured` / `metrics.saved_tokens_estimated`)
- the public landing stats (`saved_tokens_measured` / `saved_tokens_estimated` in `/api/public/stats`)

**`total_saved_tokens` is always the sum of both.** When in doubt, quote the `measured` number.

## Event types

| Event type | Label | What "original" means |
|---|---|---|
| `TOOL_READ` | measured | Full file content vs. the sliced window the model received |
| `COMMAND_FILTER` | estimated | The command is *rewritten before execution*; "original" is a heuristic estimate of the verbose output the original command would have printed (lockfile dumps, unbounded logs) |
| `GRAPH_SKELETON` / `AST_SKELETON` | measured | Full source vs. the folded skeleton the model received |
| `SCOPED_SEARCH` | measured | What a raw recursive search would have returned vs. the ranked matches |
| `DIR_LIST` | measured | Verbose `ls -la`-style listing vs. the pruned tree |
| `CONTEXT_GOVERNOR` | measured | Unmanaged context mass vs. governor-trimmed mass actually submitted |
| `OUTPUT_GUARD` | measured | Write content the model produced and would have committed wholesale; the blocked bytes are real, generated output |
| `GUARD_*`, `SECRET_GUARD` | estimated | Blocked commands/reads never run, so their "would-have-entered-context" mass is a heuristic (constant floor or `len/4`), not metered I/O |
| `LOOP_GUARD` | estimated | Identical repeat call is paused; its duplicate output mass is a fixed 5,000-token estimate |
| `GRAPH_TOPOLOGY` | estimated | A hypothetical full-file crawl the graph made unnecessary |
| `HISTORY_FOLD` | estimated | The folded digest is **injected** as a system message; the raw history physically remains in the context window. "Saved" = history mass the model no longer needs to actively attend to. |
| `PROMPT` | estimated | TOCP-compressed form of the prompt (injected alongside the original until replace-mode is verified end-to-end) |
| `ROLLUP_LIFETIME` | estimated | Pruned historical events of mixed provenance, conservatively labelled |

## Rules we hold ourselves to

1. **No synthetic fallback numbers.** If a live value (e.g. npm weekly downloads) is unavailable, the API returns `null` and the UI renders `—` — never a plausible-looking constant. Zero is a value; a fabricated number is a lie.
2. **Injection ≠ replacement.** An optimization only graduates from `estimated` to `measured` when the compact form *replaces* the original bytes in what the model actually sees.
3. **Lifetime rollups are conservative.** `lifetime_aggregates` (local) and `ROLLUP_LIFETIME` (cloud) carry pruned event mass of mixed provenance and are always labelled `estimated`.
4. **USD figures are derived**, not billed: `saved_tokens × $1.50 / 1M` (blended frontier-model pricing). They approximate developer cost avoided, not an invoice.
5. **Benchmarks are labelled.** See `BENCHMARK_REPORT.md`: sections are explicitly `MEASURED` (deterministic tool I/O deltas on real files) or `SIMULATED` (modeled agent behavior). We never present a simulated number as a measured one.

## Data flow

```
hooks (pre_invocation / pre_tool_use / post_tool_use)      MCP server (tools)
        │                                                       │
        └──────────────► ~/.tokensafe/data/metrics.db ◄─────────┘
                             │  events.measurement
                             ▼
                    telemetry sync (hourly, opt-out via
                    TOKENSAFE_TELEMETRY_DISABLED=1)
                             │  events[].measurement
                             ▼
                    cloud telemetry table
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
        dashboard sums              /api/public/stats
   (measured + estimated)     (measured + estimated)
```
