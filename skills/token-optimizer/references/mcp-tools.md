# Core MCP Tools Reference (`tokensafe`)

All tools are exposed via Model Context Protocol (stdio JSON-RPC 2.0).

| MCP Tool | Description | Efficiency Impact |
|---|---|---|
| `tokensafe:smart_read_file` | Graph-guided AST reader with `symbol` body retention, `dep_mode` (`signature` default / `full`), and session pointer dedup (`force_full`). OpenAPI/Swagger, Prisma, GraphQL and SQL files are auto-sliced: `symbol` = spec path/model/type/table; empty `symbol` returns a compact overview. | **-90% to -98%** token reduction |
| `tokensafe:smart_bundle` | Multi-symbol context bundle: up to 8 `{path, symbol}` targets in ONE call — per-file skeletons with shared import lines deduplicated and merged 1-hop dependency signatures. Replaces 3+ chained `smart_read_file` calls. | **-85% to -95%** + one roundtrip instead of many |
| `tokensafe:scoped_search` | Safe, token-bounded keyword, symbol, or config search across the codebase (supports `path='.'`, `scope='code'`/`'config'`/`'all'`/file extension/subfolder, 4s safety timeout, system root protection). | **-90% to -95%** vs raw grep/find |
| `tokensafe:list_dir` | Token-efficient directory tree without lockfiles, timestamps, or ignored folders (node_modules, .git, dist, .env). Replaces `ls -la`. | **-90%** vs verbose `ls` |
| `tokensafe:get_architecture_graph` | Computes dependency topology, hub centrality, and identifies top God Nodes for `project_path`. | **~200 tokens** (replaces 50k crawl) |
| `tokensafe:get_api_surface` | Generates a compact markdown map of all exported types, interfaces, and classes. | **~500 tokens** for entire project |
| `tokensafe:sanitize_command` | Per-stage command rewrites: lockfile diffs, unbounded `git log`/`git show`/`git blame`/`rg`, docker/kubectl log caps, quiet npm/pip installs, bounded `cat`/`curl` output. Compound commands (`a && b ; c`) are optimized per stage without breaking operators. Pass the optional `cwd` argument for test-runner commands: the guard reads `<cwd>/package.json` to identify the runner behind opaque `yarn/npm test` scripts and forces jest/vitest into run-once mode (`CI=true`, `--watch=false`, `--forceExit`) so agents never hang waiting on watch mode. | Saves **10k-50k tokens** per run |
| `tokensafe:slice_diff` | Slices unified git diffs, eliminating lockfiles and minimizing hunk context lines. | **-50% to -80%** diff size |
| `tokensafe:compress_prompt` | Compresses verbose user instructions into lean TOCP S-expressions via DeepSeek/Regex. | **-40% to -65%** prompt tokens |
| `tokensafe:compact_conversation` | On-demand context compaction: folds a long JSONL session transcript into a lean Consolidated State Ledger (`transcript_path`, optional `invocation_num`). Call when the context window feels heavy or before a new subtask. | **-80% to -95%** history tokens |
| `tokensafe:context_budget` | Context Budget HUD: estimated window usage (`model`/`window_tokens`/`session_id`) with tools/history/prompts breakdown and 50/80/95% thresholds. Call before deciding to compact. | Floor estimate, always labeled estimated |
| `tokensafe:guard_inspect` | Pre-flight security & hygiene audit: denies literal secrets/destructive commands in shell lines (secrets, TLS, SQLi, raw grep); advisory for never-terminating streams (`tail -f`, `watch`, `docker attach`) and TLS bypasses. | **Zero-Risk** pre-flight gate |
| `tokensafe:get_token_savings_metrics` | Returns cumulative analytics: tokens saved (measured/estimated split), context ratio, ≈USD value (derived), and optimization source breakdown. | Persistent SQLite analytics |

---

## Agent Workflows

### 1. Architecture Exploration Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "get_architecture_graph",
  "Arguments": {
    "project_path": "/Users/.../projects/my-app"
  }
}
```
*Identifies core architectural hubs and dependency weights before inspecting files.*

### 2. Scoped Code & Config Search Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "scoped_search",
  "Arguments": {
    "query": "HARDCODED_VERSION",
    "path": "./src",
    "scope": "code"
  }
}
```
*Locates exact occurrences with file and line references in < 100 tokens. Always pass `path` (defaults to active project root, e.g. ".") and optional `scope` ('code', 'config', 'all', file extension like 'go', or subfolder like 'internal/promo'). Auto-guarded against system roots.*

### 2b. Directory Navigation Workflow (`list_dir`)
```json
{
  "ServerName": "tokensafe",
  "ToolName": "list_dir",
  "Arguments": {
    "path": "/Users/.../projects/my-app/src",
    "depth": 2
  }
}
```
*Renders a clean directory tree without permissions, timestamps, lockfiles, or heavy directories (`node_modules`, `.git`, `dist`).*

### 3. Targeted Symbol Inspection Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "smart_read_file",
  "Arguments": {
    "path": "/Users/.../projects/my-app/src/views/StatsView.tsx",
    "symbol": "LuckyWheelTab",
    "dep_mode": "signature"
  }
}
```
*Extracts the targeted `LuckyWheelTab` implementation plus compact 1-line signatures for 1-hop dependencies with inert code and imports folded. Pass `"dep_mode": "full"` for full dependency bodies, `"include_deps": false` for 0-hop isolation, or `"force_full": true` to bypass a session pointer hit.*

### 4. Public API Map Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "get_api_surface",
  "Arguments": {
    "project_path": "/Users/.../projects/my-app"
  }
}
```
*Returns all exported interfaces and types without reading hundreds of module files.*

### 5. Pre-flight Security & Hygiene Audit
```json
{
  "ServerName": "tokensafe",
  "ToolName": "guard_inspect",
  "Arguments": {
    "command": "git add .env",
    "file_path": "src/auth.ts"
  }
}
```
*Validates commands and code snippets before execution; returns structured pass/block verdict with check_name.*

### 6. Context Budget → Smart Compact Workflow
```json
{
  "ServerName": "tokensafe",
  "ToolName": "context_budget",
  "Arguments": {
    "model": "claude-x"
  }
}
```
*Run before large multi-step tasks. When the response reports `threshold: "warning"` or `"critical"`, immediately chain the compact call:*

```json
{
  "ServerName": "tokensafe",
  "ToolName": "compact_conversation",
  "Arguments": {
    "transcript_path": "/Users/.../.claude/projects/<session>/transcript.jsonl",
    "invocation_num": 12
  }
}
```
*Folds past turns into a Consolidated State Ledger (-80% to -95% history tokens) so long sessions never degrade into context starvation. The transcript path is provided by the host session (hook payload `transcript_path` / session start context).*
