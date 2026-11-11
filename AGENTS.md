# TokenSafe Project Instructions

Welcome to the **TokenSafe** project repository.

## Project Overview
This repository implements the TokenSafe local engine for AI coding agents.
It intercepts user prompts via lifecycle hooks, compiles natural language into compact intent expressions,
slices bulky tool calls (such as full-file reads), guards secrets and unsafe commands, and provides real-time SQLite metrics tracking.

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

## Response Formatting Directive (Proof-of-Savings Footer — always in English)
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens`

<!-- tokensafe -->
## TOKENSAFE GRAPH-FIRST OPTIMIZATION PROTOCOL
Always prioritize `tokensafe` MCP tools over default raw file reads or high-output terminal executions to prevent context window exhaustion.

1. **Architecture Discovery**: Always call `tokensafe:get_architecture_graph` before grep or multi-file reading. Returns project God Nodes and key dependency hubs in < 250 tokens without file crawling.
2. **Graph-Guided Symbol Inspection**: When inspecting code implementations, prioritize `tokensafe:smart_read_file` with the targeted `symbol`. Returns full executable bodies for the symbol and 1-hop dependencies while folding inert code into signatures (-85% to -92% tokens).
3. **No Unconstrained Shell Grep/Find & No Verbose ls -la**: Strictly avoid running raw recursive grep/find across projects. Never run verbose `ls -la` or `ls -l`; use `tokensafe:list_dir(path, depth=2)` to inspect directory trees in clean markdown without permissions, timestamps, or lockfiles (-90% tokens).
4. **Command Sanitization**: Use `sanitize_command` on high-output terminal commands (`git diff/log/show/blame`, `rg`, `find`, `grep`, `ls`, `docker`/`kubectl logs`, `npm`/`pip install`, `cat` of lockfiles, `curl`) — compound commands (`a && b ; c`) are rewritten per stage. Run unit tests via the `tokensafe tests [pytest]` CLI (compact failure report) instead of raw `python3 -m unittest discover`; never leave `vitest`/`jest` in watch mode — pass the optional `cwd` to `sanitize_command` for test commands so the package.json peek can identify the runner behind opaque `yarn/npm test` scripts.
5. **Styles & Design Token Preservation**: When inspecting CSS, SCSS, LESS, or Tailwind, `smart_read_file` automatically preserves all design tokens (`:root`, `@theme`, CSS variables) in full while folding component selectors to protect project design integrity.
6. **Secret Leak Prevention Directive (Zero Hardcoded Fallbacks)**:
Never hardcode real private keys, API secrets, access tokens, or webhook secrets as default fallback arguments in `os.environ.get()`, `os.getenv()`, or `process.env`. If an environment variable is absent, raise an explicit configuration error or use an empty string / test mock. Whenever a secret fallback attempt is blocked, state:
`🛡️ **TokenSafe Security Guard**: Prevented secret / private key leak into git (<target_file>: <details>)`
7. **Proof-of-Savings Turn Footer (always in English)**:
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`
<!-- /tokensafe -->

