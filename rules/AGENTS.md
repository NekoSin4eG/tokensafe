<!--
Rule: Token-Optimized Communication Protocol (TOCP)
Trigger: always_on
-->

# Token-Optimized Communication Protocol (TOCP)

When interacting within this environment, you MUST follow strict token economy guidelines:

### 1. Ingress Intent Decoding
When a user input or system message is prefixed with `[TOCP Compressed Intent]`, it follows the lean S-Expression syntax:
`(ACTION [target_file] [scope_symbol] [[traits...]] ["residual_intent"])`

- `ACTION`: `REFACTOR`, `DEBUG`, `CREATE`, `OPTIMIZE`, `EXPLAIN`, `AUDIT`, `TEST`, `RUN`.
- `target_file`: Primary file path to inspect or edit.
- `scope_symbol`: Targeted function, method, class, or identifier.
- `traits`: Constraints such as `[async redis cache zero_alloc typesafe unit_test]`.
- `residual_intent`: Minimal context delta.

Execute the specified action directly based on these extracted parameters.

### 2. Zero-Fluff Output Directives (Egress Optimization)
1. **No Conversational Filler**: Never include courtesy greetings or sign-offs (e.g., "Certainly!", "Sure, I can help", "Let me know if you need anything else").
2. **Surgical Code Edits**: Prefer `edit_file` with concise `Target` and `Replacement` over `write_file` on large existing files.
3. **Diff-Centric Responses**: Never reprint entire unmodified files or long boilerplate blocks. Reference file lines instead (e.g., `wallet.ts#L42-L50`).
4. **Dense Formatting**: Use compact bullet points and direct technical prose.

### 3. Architecture & Code Navigation Directives (Graph-Guided)
1. **Zero Raw Terminal Grep/Find (Blocked)**: Strictly NEVER execute terminal `grep`, `egrep`, `ripgrep`, or `find` on source code files or directories (`src/`, `views/`, `components/`, `.ts`, `.tsx`, `.py`, `.js`). These commands are hard-blocked by TokenSafe Guard.
2. **Mandatory Symbol & Component Inspection**: Whenever locating or reading a function, class, component, or method, ALWAYS call `tokensafe:smart_read_file` with `path` and `symbol`. By default (`dep_mode="signature"`), it preserves the full executable target and compact 1-line 1-hop dependency signatures while folding inert code (-95%+ tokens). Pass `dep_mode="full"` only when dependency bodies are needed, or `force_full=true` if a cache pointer references content missing from your context.
3. **Architecture Discovery**: Before executing multi-file exploration, ALWAYS call `tokensafe:get_architecture_graph(project_path)` to locate God Nodes and dependency topology in < 250 tokens without file crawling.
4. **API Surface Map**: Use `tokensafe:get_api_surface(project_path)` to get all exported classes, types, and interfaces in a single markdown map.
5. **Bounded Reads**: Never call `read_file` without `StartLine`/`EndLine` on files > 100 lines unless inspecting a skeleton.
6. **Directory Navigation**: Never run verbose `ls -la` or `ls -l`; use `tokensafe:list_dir(path, depth=2)` to inspect directory trees without permissions, timestamps, or lockfiles (-90% tokens).
7. **Style & Design Tokens**: When inspecting CSS/SCSS/LESS/Tailwind, `tokensafe:smart_read_file` automatically preserves all design tokens (`:root`, `@theme`, CSS variables) in full while folding component selectors.
8. **Context Budget & Smart Compaction**: Before large multi-step tasks, call `tokensafe:context_budget(model=...)` to check window usage (tools/history/prompts breakdown, 50/80/95% thresholds). When it reports `warning` or `critical`, call `tokensafe:compact_conversation(transcript_path=...)` to fold past turns into a State Ledger instead of letting code quality degrade.

### 4. Turn Token Savings Footer (Proof-of-Savings — always in English)
At the end of your response for any turn where tools were executed, files were inspected, commands were sanitized, or prompts were compacted, append a compact 1-line footer:
`⚡ **TokenSafe Turn Savings**: +<tokens> tokens saved on this turn (<breakdown>) | Cumulative Total: <total> tokens (≈$<usd> approx)`
Example:
`⚡ **TokenSafe Turn Savings**: +3,520 tokens (~88%) | Cumulative Total: 3.04M tokens (≈$4.56 approx)`

### 5. Secret Leak Prevention Directive (Zero Hardcoded Fallbacks)
1. **Never Hardcode Credential Fallbacks**: Never provide real private keys, API secrets, access tokens, or webhook secrets as default fallback arguments in `os.environ.get()`, `os.getenv()`, or `process.env`. If an environment variable is absent, raise an explicit configuration error or use an empty string / test mock. Real credentials must exist strictly in environment variables.
2. **Leak Prevention Notification (English)**:
Whenever a tool execution, code edit, or command attempts to write or commit a hardcoded secret fallback, block it and include an explicit notification alert in your response:
`🛡️ **TokenSafe Security Guard**: Prevented secret / private key leak into git (<target_file>: <details>)`
