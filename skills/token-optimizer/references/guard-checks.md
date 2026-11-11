# Security & Hygiene Engine (TokenSafe Guard)

TokenSafe Guard automatically intercepts dangerous operations in `PreToolUse`:

| Guard Check | Monitored Operation | Action Taken |
|---|---|---|
| **RAW_CODE_SEARCH** | Terminal `grep`, `egrep`, `rg`, `find` on source code | **Blocked** (`decision: deny`), directs to `scoped_search` / `smart_read_file` |
| **SECRET_FALLBACK** | Hardcoded secrets in `os.environ.get()` / `process.env` | **Blocked** (`decision: deny`), prevents credential commit |
| **TLS_VERIFY_BYPASS** | Insecure TLS bypass (`verify=False` / `rejectUnauthorized: false`) | **Blocked** (`decision: deny`), MitM attack prevention |
| **SQL_INJECTION** | Unsafe f-string / template string interpolation in queries | **Blocked** (`decision: deny`), enforces parameterized queries |
| **GIT_SECRET_STAGING** | `git add .env` or blanket staging with unignored secrets | **Blocked** (`decision: deny`), prevents staging credentials |
| **DESTRUCTIVE_RM** | `rm -rf /`, `rm -rf ~`, `rm -rf *` | **Blocked** (`decision: deny`), prevents catastrophic deletion |
| **DATA_EXFILTRATION** | Piping `.env` or private keys to `curl`/`wget` | **Blocked** (`decision: deny`), prevents secret leakage |
| **PACKAGE_TYPOSQUAT** | Installing known typosquatted packages | **Warning** injected into turn context |
| **INFINITE_STREAM** (adapters) | Never-terminating commands: `tail -f`, `watch`, `docker attach`, `docker/kubectl logs -f`, `vitest` (watch by default — use `vitest run`), `jest`/`vitest --watch` | **Asked** (`decision: ask`) — intentional runs proceed via confirmation |

Adapter-level checks (Claude Code / Cursor / ZCode hooks) classify command-level TLS bypass (`curl -k`, `git -c http.sslVerify=false`, `GIT_SSL_NO_VERIFY=1`) and infinite streams as `ask`, and hard-deny raw recursive code search (`grep -rn src/`, `rg`, `find` over source trees) as `RAW_CODE_SEARCH` with a redirect to `tokensafe:scoped_search` — TokenSafe Guard's code-level checks above remain `deny`.
