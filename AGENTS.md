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
Before submitting changes, keep new dependencies minimal: the AST, proxy, MCP, and telemetry engines run strictly on the Python standard library, with `cryptography>=42.0` (see `requirements.txt` / `pyproject.toml`) as the single required runtime dependency for Ed25519 offline license verification and fail-closed RS256 Clerk session verification.

<!-- tokensafe -->
## TOKENSAFE GRAPH-FIRST OPTIMIZATION PROTOCOL
Always use `tokensafe` MCP tools over default raw file reads or high-output terminal executions to prevent context window exhaustion.

1. **Zero Raw Grep/Find (Blocked)**: Strictly NEVER execute terminal `grep`, `egrep`, `ripgrep`, or `find` on source code files or directories (`src/`, `views/`, `components/`, `.ts`, `.tsx`, `.py`, `.js`). These commands are hard-blocked by TokenSafe Guard.
2. **Mandatory Symbol & Component Inspection**: Whenever locating or reading a function, class, component, or method, ALWAYS call `tokensafe:smart_read_file` with `path` and `symbol`. By default (`dep_mode='signature'`), it preserves the target body and compact 1-line 1-hop dependency signatures (-95%+ tokens); pass `dep_mode='full'` only when dependency bodies are required, or `force_full=true` if a cache pointer references content missing from your context. When you need 3+ related symbols across several files, call `tokensafe:smart_bundle(targets=[{path, symbol}])` ONCE instead of repeated `smart_read_file` calls — it merges the skeletons and deduplicates shared imports/types in a single roundtrip. For OpenAPI/Prisma/GraphQL/SQL schema files `smart_read_file` auto-slices: pass the spec path/model/type/table as `symbol` (empty `symbol` returns a compact overview).
3. **Repeat Read Deduplication**: Never repeatedly re-read unchanged files or symbols within the same session. Reuse the earlier skeleton or pointer hit (`// [TokenSafe: Pointer Hit ...]`); pass `force_full=true` only if context was compacted or you are a fresh subagent.
4. **Zero-Fluff Egress & Surgical Diffs**: Omit conversational filler, courtesy preambles, and sign-offs. Provide concise bullet prose. Never reprint full unmodified files or lengthy boilerplate; link to file line ranges (`file.ts#L10-L25`) and output surgical diffs.
5. **Architecture Discovery**: Always call `tokensafe:get_architecture_graph(project_path)` before exploratory multi-file reading. Returns project God Nodes and key dependency hubs in < 250 tokens without file crawling.
6. **API Surface Map**: Use `tokensafe:get_api_surface(project_path)` to get all exported classes, types, and interfaces in a single markdown map.
7. **No Unconstrained Shell Grep/Find & No Verbose ls -la**: Strictly avoid running raw recursive grep/find across projects. Never run verbose `ls -la` or `ls -l`; use `tokensafe:list_dir(path, depth=2)` to inspect directory trees in clean markdown without permissions, timestamps, or lockfiles (-90% tokens).
8. **Command Sanitization & Compact Tests**: Use `sanitize_command` on high-output terminal commands (`git diff/log/show/blame`, `rg`, `find`, `grep`, `docker`/`kubectl logs`, `npm`/`pip install`, `cat` of lockfiles, `curl`) — compound commands (`a && b ; c`) are rewritten per stage. Run unit tests with concise flags or via the `tokensafe tests [cmd]` CLI (compact passing summary `PASS: ...`, full traceback only on failure) instead of raw verbose test crawls; never leave `vitest`/`jest` in watch mode — pass the optional `cwd` to `sanitize_command` for test commands so the package.json peek can identify the runner behind opaque `yarn/npm test` scripts.
9. **Styles & Design Token Preservation**: When inspecting CSS, SCSS, LESS, or Tailwind, `smart_read_file` automatically preserves all design tokens (`:root`, `@theme`, CSS variables) in full while folding component selectors to protect project design integrity.
10. **Context Budget & Smart Compaction**: Before large multi-step tasks, call `tokensafe:context_budget(model=...)` to inspect window usage; when it reports `warning` or `critical`, call `tokensafe:compact_conversation(transcript_path=...)` before continuing.
11. **Secret Leak Prevention Directive (Zero Hardcoded Fallbacks)**:
Never hardcode real private keys, API secrets, access tokens, or webhook secrets as default fallback arguments in `os.environ.get()`, `os.getenv()`, or `process.env`. If an environment variable is absent, raise an explicit configuration error or use an empty string / test mock. Whenever a secret fallback attempt is blocked, state:
`🛡️ **TokenSafe Security Guard**: Prevented secret / private key leak into git (<target_file>: <details>)`
12. **Proof-of-Savings Turn Footer (always in English)**:
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`
<!-- /tokensafe -->

