# Agent Setup Reference

Quick start lives in the [README](../README.md). This page is the full integration reference: what `tokensafe setup` configures per agent, how lifecycle hooks work, and how to connect the cloud MCP endpoint.

## 1. One-Command Install

```bash
# Option 1: Zero-Install via npx (recommended)
npx tokensafe

# Option 2: Python / PyPI
pip install tokensafe && tokensafe

# Option 3: Direct shell script (macOS / Linux)
curl -fsSL https://tokensafe.dev/install.sh | bash
```

The installer detects which AI coding agents live on your machine, registers the TokenSafe MCP server, injects project rules, and (where the agent supports it) wires lifecycle hooks. Run `tokensafe setup --all` to configure every detected agent non-interactively.

## 2. Supported Agents

| Agent | Integration | Notes |
|---|---|---|
| **Google Antigravity** | Plugin symlink + hooks + skills + MCP | Loaded automatically on startup; see migration notes below |
| **Claude Code CLI** | MCP + `SessionStart` / `UserPromptSubmit` / `PreToolUse` hooks + rules | Deepest hook integration |
| **Cursor** | MCP + `beforeShellExecution` / `beforeReadFile` hooks + rules | Strict valid-JSON hook contract preserved on every error path |
| **Claude Desktop** | MCP registration (`claude_desktop_config.json`) | Stdio server |
| **Windsurf** | MCP registration + global rules | `~/.codeium/windsurf/mcp_config.json` |
| **OpenAI Codex** | MCP in `~/.codex/config.toml` + `AGENTS.md` rules | Supports both stdio and the cloud streamable-HTTP endpoint natively |
| **Cline / Roo Code (VS Code)** | MCP registration | Stdio server |
| **GitHub Copilot** | MCP registration | Stdio server |
| **Z.ai Code** | MCP registration | Stdio server |
| **ChatGPT** | Cloud connector, nothing to install | Uses `https://api.tokensafe.dev/mcp` |
| **Gemini CLI** | MCP registration | Stdio server |

Run `get_setup_instructions` through any connected agent for copy-paste snippets per target: `cursor`, `claude_desktop`, `claude_code`, `codex`, `windsurf`, `antigravity`, `gemini_cli`, `chatgpt`.

## 3. Lifecycle Hooks

Beyond MCP, `tokensafe setup` installs native hook adapters inside the agent loop:

| Agent | Hook | What it does |
|---|---|---|
| **Claude Code** | `SessionStart` | Injects TokenSafe protocol directives once per session as `additionalContext` |
| **Claude Code** | `UserPromptSubmit` | Compacts the prompt into a compact intent expression and injects it as `additionalContext` (labeled *estimated* per [METRICS.md](../METRICS.md)) |
| **Claude Code** | `PreToolUse` (Bash) | Blocks secret-bearing and destructive commands; prompts for confirmation on TLS-verification bypasses |
| **Cursor** | `beforeShellExecution` | Same shell guard: blocks secret leaks and destructive actions, asks on `-k` / `--insecure` |
| **Cursor** | `beforeReadFile` | Blocks raw lockfile, bundle, and `dist/` reads that would burn ~50k context tokens |

All adapters **fail open**: an internal error never stalls the host agent. Hook failures are logged locally, never surfaced as agent-blocking errors.

## 4. Team Workspaces (Antigravity)

For team workspaces, `tokensafe install --project --agents-plugin <path-to-tokensafe>` declares the plugin in the project's `.agents/plugins.json`, so teammates load hooks, skills, and the MCP server straight from the shared repository without running the installer.

## 5. Antigravity Migration Notes

- The installer symlinks the plugin into `~/.gemini/config/plugins/tokensafe`. Antigravity loads it automatically on startup.
- Stale `agy-token-optimizer` symlinks from older versions are removed automatically: Antigravity keys plugin enable-state by folder name, and a leftover copy would run every hook twice.
- Once the plugin ships its own `mcp_config.json`, any leftover user-level `tokensafe` MCP entry in Antigravity's configs is migrated away (backed up first) so the server is not registered twice.

## 6. Optional Dependency: `cryptography`

The local TokenSafe engine uses Python's standard library only and runs out of the box. One optional package, [`cryptography`](https://pypi.org/project/cryptography/), is installed automatically by `install.sh` and `requirements.txt` when available:

```bash
pip install cryptography   # or: brew install cryptography
```

| Component | With `cryptography` | Without (pure-stdlib fallback) |
|---|---|---|
| Client: license verification | Instant Ed25519 fast-path | Pure-Python Ed25519 (~0.3s once, then cached) |
| Client: everything else | Identical | Identical |
| Cloud backend: Clerk session tokens | RS256 signature verified | **Rejected** (fail-closed: unsigned tokens are never accepted) |
| Cloud backend: everything else | Identical | Identical |

The client caches a verified license for up to 1 hour, so even the pure-Python path adds no measurable latency after the first hook invocation.

## 7. Cloud MCP Endpoint

The stdio server slices your local files. Cloud features (team savings metrics, license status, server-side prompt distillation, shared team cache, billing) live on the remote streamable-HTTP endpoint:

```
https://api.tokensafe.dev/mcp
```

Auth: `Authorization: Bearer <TS1./TS2. license key>` (from [tokensafe.dev](https://tokensafe.dev)). Four public tools work without a key: `get_public_stats`, `check_latest_version`, `get_setup_instructions`, `search_docs`.

### Codex CLI (`~/.codex/config.toml`, shared with the IDE extension)

```toml
[mcp_servers.tokensafe]              # local engine: AST slicing, guards
command = "npx"
args = ["-y", "tokensafe", "mcp"]

[mcp_servers.tokensafe-cloud]        # cloud features
url = "https://api.tokensafe.dev/mcp"
bearer_token_env_var = "TOKENSAFE_LICENSE"
```

Set `export TOKENSAFE_LICENSE="TS2.…"`, then run `get_setup_instructions` via any connected agent.

### ChatGPT (custom connector, nothing to install)

1. **Settings → Connectors → Create**, paste `https://api.tokensafe.dev/mcp`
2. Authentication: **None** — the four public tools work immediately
3. Ask ChatGPT to "show TokenSafe public stats" or "search TokenSafe docs"

License-gated tools (team metrics, distillation) return a clear upgrade hint when called without a key. The funnel is intentional: see the value, then bring the same endpoint into Codex / Claude Code with your Bearer key to unlock the paid tools.
