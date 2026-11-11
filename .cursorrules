<!-- tokensafe -->
## TOKENSAFE GRAPH-FIRST OPTIMIZATION PROTOCOL
Always prioritize `tokensafe` MCP tools over default raw file reads or high-output terminal executions to prevent context window exhaustion.

1. **Architecture Discovery**: Always call `tokensafe:get_architecture_graph` before grep or multi-file reading. Returns project God Nodes and key dependency hubs in < 250 tokens without file crawling.
2. **Graph-Guided Symbol Inspection**: When inspecting code implementations, prioritize `tokensafe:smart_read_file` with the targeted `symbol`. Returns full executable bodies for the symbol and 1-hop dependencies while folding inert code into signatures (-85% to -92% tokens).
3. **No Unconstrained Shell Grep/Find**: Strictly avoid running raw recursive grep/find across projects. Use `get_architecture_graph` or `get_api_surface`.
4. **Command Sanitization**: Use `sanitize_command` on high-output terminal commands (`git diff/log/show/blame`, `rg`, `find`, `grep`, `docker`/`kubectl logs`, `npm`/`pip install`, `cat` of lockfiles, `curl`) — compound commands (`a && b ; c`) are rewritten per stage. Run unit tests via the `tokensafe tests [pytest]` CLI (compact failure report) instead of raw `python3 -m unittest discover`; never leave `vitest`/`jest` in watch mode — pass the optional `cwd` to `sanitize_command` for test commands so the package.json peek can identify the runner behind opaque `yarn/npm test` scripts.
5. **Proof-of-Savings Turn Footer (English default)**:
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer in English:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens ($<usd>)`
<!-- /tokensafe -->