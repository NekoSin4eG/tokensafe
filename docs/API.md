# Cloud API & Security Reference

TokenSafe Cloud enforces strict zero-trust boundaries between the local client and the backend. This page documents the client-facing API surface and the cryptographic model behind it. Operational/admin endpoints are not part of the public surface and are intentionally undocumented here.

## Security Architecture

- **Asymmetric RFC 8032 Ed25519 signatures (`TS2.*`)**: client instances verify license signatures offline using an embedded 32-byte public key. The private signing key never leaves the cloud environment.
- **JWT session tokens**: cloud endpoints verify sessions via HS256 JWT bearer tokens or Clerk session tokens.
- **Dual bearer support**: client endpoints accept either a signed license key (`TS2.*` / `TS1.*`) or a JWT in `Authorization: Bearer <token>`.
- **Zero hardcoded secrets**: all server credentials load strictly from runtime environment variables. Nothing is hardcoded in the codebase.

## Cloud API Endpoints

| Method | Endpoint | Auth Required | Headers / Parameters | Description |
|---|---|---|---|---|
| `GET` | `/api/health` | Public | None | Healthcheck and service version status. |
| `GET` | `/api/events` | Public | SSE `text/event-stream` | Real-time Server-Sent Events stream for live dashboard telemetry. |
| `GET` | `/api/dashboard` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>` or `?license=<key>` | Authenticated user metrics, token savings, and license status. |
| `GET` | `/api/digest/preview` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>` or `?team_id=<id>` | Previews current weekly team ROI digest (HTML / JSON). |
| `POST` | `/api/team/seat/create` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>`, JSON `{ owner_email, member_email }` | Generates a cryptographically signed seat license for a team member. |
| `POST` | `/api/team/seat/revoke` | **Bearer Auth** | `Authorization: Bearer <jwt\|key>`, JSON `{ owner_email, license_key }` | Revokes an active team seat license key. |
| `POST` | `/api/telemetry/sync` | **License Auth** | JSON `{ license_key, email, events: [...] }` | Syncs batch telemetry events (enforces max 50 events/batch). |

## Remote MCP Endpoint

The same capabilities are also exposed as MCP tools over streamable HTTP at `https://api.tokensafe.dev/mcp` (see [Agent Setup](AGENT_SETUP.md#7-cloud-mcp-endpoint)). Thirteen tools total: four public (`get_public_stats`, `check_latest_version`, `get_setup_instructions`, `search_docs`) and nine license-gated (`get_license_status`, `get_team_metrics`, `distill_prompt`, `team_cache_lookup`, `team_cache_store`, `list_invoices`, `generate_team_seat`, `revoke_team_seat`, `preview_weekly_digest`).

## Seat & License Management

Seat and license management is available through the [dashboard](https://dashboard.tokensafe.dev): create or revoke per-seat `TS2.*` keys, review per-seat ROI, and inspect team metrics.

## Privacy Controls

- **Telemetry opt-out**: set `TOKENSAFE_TELEMETRY_DISABLED=1` to disable all cloud telemetry sync. Local metrics keep working; nothing leaves the machine.
- **Data location**: local metrics live in `~/.tokensafe/data/` (override with `TOKENSAFE_DATA_DIR`). Source code is never transmitted: slicing, guarding, and compaction run locally on file contents that never leave the disk.
- **Zero source code transmitted**: the cloud sees only counters (token totals, event types), never file contents or prompts. Full accounting contract: [METRICS.md](../METRICS.md).
