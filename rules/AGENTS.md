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
1. **Never Crawl with Raw Grep/Find**: Before executing exploratory searches or answering architecture questions, inspect `tokensafe:get_architecture_graph` or `tokensafe-out/API_SURFACE.md`. It provides project God Nodes and dependencies in < 250 tokens without file crawling.
2. **Graph-Guided File Reading**: When inspecting code implementations, call `tokensafe:smart_read_file` with the targeted `symbol`. It preserves full executable bodies for the target function and its 1-hop callees while folding inert code into signatures (-85% to -92% tokens).
3. **Bounded Reads**: Never call `read_file` without `StartLine`/`EndLine` on files > 100 lines unless inspecting a skeleton.
