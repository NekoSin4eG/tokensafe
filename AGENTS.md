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
- `bin/tokensafe`: Standalone executable CLI for developers.

## Testing & Quality
Run unit tests with:
```bash
python3 -m unittest discover tests
```
Before submitting changes, ensure no new external runtime dependencies are introduced (standard library only for maximum portability). The single sanctioned exception is the optional `cryptography` package (see `requirements.txt`): it accelerates Ed25519 license verification on clients and is required for fail-closed RS256 verification of Clerk session tokens on the cloud backend.

## Agent Code Inspection Protocol (TokenSafe MCP)
- **Architecture Navigation**: Always call `tokensafe:get_architecture_graph` before grep or multi-file reading.
- **Symbol Inspection**: Always call `tokensafe:smart_read_file` with `symbol` specified to retrieve graph-guided slices with 1-hop dependencies.
- **Raw Grep**: Strictly prohibited for architectural surveys. Use `tokensafe-out/API_SURFACE.md` or `get_architecture_graph`.

## Response Formatting Directive (Proof-of-Savings Footer)
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens`

<!-- tokensafe -->
## TOKENSAFE GRAPH-FIRST OPTIMIZATION PROTOCOL
Always prioritize `tokensafe` MCP tools over default raw file reads or high-output terminal executions to prevent context window exhaustion.

1. **Architecture Discovery**: Always call `tokensafe:get_architecture_graph` before grep or multi-file reading. Returns project God Nodes and key dependency hubs in < 250 tokens without file crawling.
2. **Graph-Guided Symbol Inspection**: When inspecting code implementations, prioritize `tokensafe:smart_read_file` with the targeted `symbol`. Returns full executable bodies for the symbol and 1-hop dependencies while folding inert code into signatures (-85% to -92% tokens).
3. **No Unconstrained Shell Grep/Find & No Verbose ls -la**: Strictly avoid running raw recursive grep/find across projects. Never run verbose `ls -la` or `ls -l`; use `tokensafe:list_dir(path, depth=2)` to inspect directory trees in clean markdown without permissions, timestamps, or lockfiles (-90% tokens).
4. **Command Sanitization**: Use `sanitize_command` on high-output terminal commands (`git diff`, `pytest`, `find`, `grep`, `ls`) to prevent lockfile or test log context contamination.
5. **Styles & Design Token Preservation**: When inspecting CSS, SCSS, LESS, or Tailwind, `smart_read_file` automatically preserves all design tokens (`:root`, `@theme`, CSS variables) in full while folding component selectors to protect project design integrity.
6. **Secret Leak Prevention Directive (Zero Hardcoded Fallbacks)**:
Never hardcode real private keys, API secrets, access tokens, or webhook secrets as default fallback arguments in `os.environ.get()`, `os.getenv()`, or `process.env`. If an environment variable is absent, raise an explicit configuration error or use an empty string / test mock. Whenever a secret fallback attempt is blocked, state:
`🛡️ **TokenSafe Security Guard**: Prevented secret / private key leak into git (<target_file>: <details>)`
7. **Proof-of-Savings Turn Footer (English default)**:
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`
<!-- /tokensafe -->

<!-- bunny-cli:start -->

## bunny.net CLI

This project uses bunny.net. Manage its resources with the `bunny` CLI: databases, DNS, storage, Edge Scripts, static sites, and cloud sandboxes.

- Authenticate once with `bunny login` (or set `BUNNYNET_API_KEY`); verify with `bunny api GET /user`.
- Discover commands with `bunny --help` and `bunny <namespace> --help`; resource commands support `--output json` for machine-readable output (a few browser-opening helpers like `bunny docs` do not).
- In unattended runs, pass a flag for every value a command would prompt for, and `--force` on destructive commands; prompts otherwise block or cancel without a TTY.
- Key namespaces: `bunny db` (Bunny Database: create, shell, studio, tokens), `bunny dns` (zones, records, presets), `bunny sites` (static hosting and deploys), `bunny scripts` (Edge Scripts), `bunny storage` (Edge Storage zones and files), `bunny sandbox` (cloud sandboxes).
- To query a Bunny Database from application code, use `@bunny.net/database-client`; it is server-side only, since an auth token grants access to the whole database.
- When the CLI has no command for something, fall back to `bunny api <METHOD> <path>` against api.bunny.net.

<!-- bunny-cli:end -->
