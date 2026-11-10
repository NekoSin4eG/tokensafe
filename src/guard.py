"""
TokenSafe Guard: Security & Hygiene Guardian for AI Coding Assistants.
Protects local workspaces and repository integrity by intercepting dangerous operations:
1. Secret & Private Key Leak Prevention (Zero Hardcoded Fallbacks)
2. Insecure SSL/TLS Verification Bypass Detection (verify=False)
3. Raw SQL Injection Prevention (String Interpolation in queries)
4. Dangerous CORS Wildcard with Credentials Detection
5. Git Staging Protection for Sensitive Files (.env*, *.key, *.pem)
6. Destructive Shell Command & Unbounded Deletion Prevention (rm -rf /)
7. Network Data Exfiltration Prevention (piping secrets to curl/wget)
8. Package Typosquatting & Hallucination Early Warning
"""

import os
import re
import fnmatch
from typing import Tuple, Optional, Dict, Any, List, NamedTuple
from src.secret_redactor import SecretRedactor

class GuardResult(NamedTuple):
    blocked: bool
    reason: Optional[str] = None
    check_name: Optional[str] = None
    warning_only: bool = False

class TokenSafeGuard:
    """Security and hygiene enforcement engine for agentic tool use."""

    KNOWN_TYPOSQUATS = {
        "python-crypto", "requests-html5", "colorama-v2", "discord-self",
        "setup-tools", "py-cord-v2", "expressjs-router", "cross-env-v2",
        "loadsh", "coookie-parser", "mongose", "dot-env"
    }

    SENSITIVE_GIT_FILES = (
        ".env", ".env.local", ".env.production", ".env.staging", ".env.development",
        "id_rsa", "id_ed25519", "credentials.json", "service_account.json"
    )

    CODE_EXTENSIONS = (
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".py", ".pyw",
        ".go", ".rs", ".java", ".cpp", ".cc", ".c", ".h", ".hpp",
        ".php", ".rb", ".vue", ".svelte"
    )

    CODE_DIRS = (
        "src", "lib", "app", "views", "components", "pages", "routes",
        "services", "controllers", "models", "internal", "pkg", "hooks"
    )

    @classmethod
    def is_doc_file(cls, file_path: str) -> bool:
        """Determines if the targeted file is documentation or non-executable text."""
        if not file_path:
            return False
        return file_path.lower().endswith((".md", ".markdown", ".rst", ".txt", ".json", ".yaml", ".yml"))

    @classmethod
    def is_test_file(cls, file_path: str) -> bool:
        """Determines if the targeted file is part of a test suite or mock fixture."""
        if not file_path:
            return False
        p = file_path.lower()
        return any(part in p for part in ("test", "mock", "fixture", "spec", "__tests__"))

    @classmethod
    def inspect_file_write(cls, file_path: str, code_snippet: str) -> GuardResult:
        """
        Audits file writes and surgical code edits before applying to disk.
        Returns GuardResult with blocked status and actionable explanation.
        """
        if not code_snippet or not isinstance(code_snippet, str):
            return GuardResult(blocked=False)

        is_test = cls.is_test_file(file_path)
        is_doc = cls.is_doc_file(file_path)
        is_scanner = bool(file_path and os.path.basename(file_path) in ("guard.py", "secret_redactor.py"))
        skip_code_checks = is_test or is_doc or is_scanner

        # 1. Secret & Private Key Fallback Guard
        is_leak, leak_reason = SecretRedactor.check_secret_fallback(code_snippet, file_path=file_path)
        if is_leak:
            return GuardResult(
                blocked=True,
                check_name="SECRET_FALLBACK",
                reason=f"{leak_reason} in '{os.path.basename(file_path) if file_path else 'code'}'"
            )

        # 2. Insecure SSL/TLS Verification Bypass Detection
        if not skip_code_checks:
            # Python: verify=False in requests, httpx, urllib3, aiohttp
            if re.search(r"\bverify\s*=\s*False\b", code_snippet):
                return GuardResult(
                    blocked=True,
                    check_name="TLS_VERIFY_BYPASS",
                    reason=(
                        f"Insecure SSL/TLS verification bypass detected ('verify=False') in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Disabling TLS certificate checks exposes connections to Man-in-the-Middle (MitM) attacks."
                    )
                )

            # Node.js: NODE_TLS_REJECT_UNAUTHORIZED = '0' or rejectUnauthorized: false
            if re.search(r"""(?:NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['"]0['"]|\brejectUnauthorized\s*:\s*false\b)""", code_snippet):
                return GuardResult(
                    blocked=True,
                    check_name="TLS_VERIFY_BYPASS",
                    reason=(
                        f"Insecure SSL/TLS verification bypass detected ('rejectUnauthorized: false' or 'NODE_TLS_REJECT_UNAUTHORIZED=0') in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Disabling TLS certificate validation exposes connections to Man-in-the-Middle (MitM) attacks."
                    )
                )

        # 3. Raw SQL Injection in Query Execution
        if not skip_code_checks:
            # Python: cursor.execute(f"...") or conn.execute(f"...")
            py_fstring_sql = re.search(
                r"""\b(?:cursor|conn|connection|db)\.execute\s*\(\s*f["'](?:\s*SELECT|\s*INSERT|\s*UPDATE|\s*DELETE|\s*DROP|\s*ALTER)""",
                code_snippet,
                re.IGNORECASE
            )
            if py_fstring_sql:
                return GuardResult(
                    blocked=True,
                    check_name="SQL_INJECTION",
                    reason=(
                        f"Potential SQL injection detected: f-string interpolation inside query execution in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Use parameterized queries with bound placeholders ('?' or '%s') instead of dynamic string formatting."
                    )
                )

            # Python: .execute("... %s ..." % (...)) or .format(...)
            py_format_sql = re.search(
                r"""\b(?:cursor|conn|connection|db)\.execute\s*\(\s*["'](?:\s*SELECT|\s*INSERT|\s*UPDATE|\s*DELETE)[^"']+['"]\s*(?:%|\.format\s*\()""",
                code_snippet,
                re.IGNORECASE
            )
            if py_format_sql:
                return GuardResult(
                    blocked=True,
                    check_name="SQL_INJECTION",
                    reason=(
                        f"Potential SQL injection detected: string '%'/format() interpolation inside query execution in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Use parameterized queries with bound placeholders instead of string formatting."
                    )
                )

            # JS/TS: db.query(`SELECT ... ${var}`) or pool.query(`... ${var}`)
            js_template_sql = re.search(
                r"""\b(?:db|pool|client|connection|knex)\.(?:query|execute|raw)\s*\(\s*`(?:\s*SELECT|\s*INSERT|\s*UPDATE|\s*DELETE|\s*DROP)[^`]*\$\{[^}]+\}""",
                code_snippet,
                re.IGNORECASE
            )
            if js_template_sql:
                return GuardResult(
                    blocked=True,
                    check_name="SQL_INJECTION",
                    reason=(
                        f"Potential SQL injection detected: template literal interpolation '${{...}}' inside query execution in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Use parameterized queries with bound placeholders ('$1', '$2' or '?') instead of string template interpolation."
                    )
                )

        # 4. Dangerous CORS Wildcard with Credentials
        if not skip_code_checks:
            has_wildcard_origin = bool(re.search(
                r"""(?:Access-Control-Allow-Origin['"]?\s*[:=,]\s*['"]\*['"]|origin\s*:\s*['"]\*['"])""",
                code_snippet,
                re.IGNORECASE
            ))
            has_credentials_true = bool(re.search(
                r"""(?:Access-Control-Allow-Credentials['"]?\s*[:=,]\s*['"]?true['"]?|credentials\s*:\s*true)""",
                code_snippet,
                re.IGNORECASE
            ))
            if has_wildcard_origin and has_credentials_true:
                return GuardResult(
                    blocked=True,
                    check_name="INSECURE_CORS",
                    reason=(
                        f"Insecure CORS configuration detected in '{os.path.basename(file_path) if file_path else 'code'}': "
                        f"'Access-Control-Allow-Origin: *' cannot be combined with 'Access-Control-Allow-Credentials: true'. "
                        f"Browsers reject this combination, and it poses significant cross-origin security risks. Specify explicit trusted origins."
                    )
                )

        return GuardResult(blocked=False)

    @classmethod
    def inspect_command(cls, command: str, cwd: str = "") -> GuardResult:
        """
        Audits shell and terminal commands before execution.
        Returns GuardResult with blocked status or non-blocking warnings.
        """
        if not command or not isinstance(command, str):
            return GuardResult(blocked=False)

        cmd = command.strip()

        # 1. Destructive Unbounded File Deletion (rm -rf / or rm -rf ~ or rm -rf *)
        rm_dangerous = re.search(
            r"""\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(?:/|~|\$HOME|\.\.|\.|\.\/|\*|/\*|\.\/\*)(?:\s|$|;|&|\|)""",
            cmd
        )
        if rm_dangerous or re.search(r"""\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+--no-preserve-root\b""", cmd):
            return GuardResult(
                blocked=True,
                check_name="DESTRUCTIVE_RM",
                reason=(
                    f"Destructive unbounded file deletion blocked: '{cmd}'. "
                    f"Running 'rm -rf' on root, home, parent directories, or wildcards poses catastrophic data loss risk."
                )
            )

        # 2. Git Staging Protection for Sensitive Files (.env, private keys)
        # Explicit git add of sensitive file
        git_add_explicit = re.search(
            r"""\bgit\s+add\s+(?:.*?\s+)?(?:['"]?(?:\.env|\.env\.[a-zA-Z0-9_-]+|[^'"]*\.pem|[^'"]*\.key|id_rsa|id_ed25519|credentials\.json)['"]?)(?:\s|$|;|&|\|)""",
            cmd,
            re.IGNORECASE
        )
        if git_add_explicit:
            return GuardResult(
                blocked=True,
                check_name="GIT_SECRET_STAGING",
                reason=(
                    f"Blocked staging of sensitive credential file into git repository: '{cmd}'. "
                    f"Environment files and private keys must be added to .gitignore and never committed to source control."
                )
            )

        # Catch blanket 'git add .' or 'git add -A' when sensitive files exist in directory
        if re.search(r"""\bgit\s+(?:add\s+(?:\.|\-A|\*)|commit\s+-[a-zA-Z]*a)""", cmd):
            target_dir = cwd or os.getcwd()
            for root, dirs, files in os.walk(target_dir):
                # Prune common heavy/inert subtrees
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".turbo")]
                for f in files:
                    if f in cls.SENSITIVE_GIT_FILES or (f.startswith(".env") and not f.endswith(".example")):
                        rel_path = os.path.relpath(os.path.join(root, f), target_dir)
                        # Check if explicitly ignored
                        if not cls._is_git_ignored(os.path.join(root, f), target_dir):
                            return GuardResult(
                                blocked=True,
                                check_name="GIT_SECRET_STAGING",
                                reason=(
                                    f"Blocked blanket git staging: '{cmd}'. "
                                    f"Unignored sensitive file '{rel_path}' detected in working tree. "
                                    f"Add '{rel_path}' to .gitignore or stage files explicitly to prevent credential leaks into git."
                                )
                            )

        # 3. Network Data Exfiltration Prevention (piping secrets to curl/wget)
        exfil_pattern = re.search(
            r"""\b(?:curl|wget|nc|ncat|netcat)\b.*?(?:\$\(cat\s+[^)]*(?:\.env|id_rsa|\.key|\.pem|secret|password|token)[^)]*\)|`cat\s+[^`]*(?:\.env|id_rsa|\.key|\.pem|secret|password|token)[^`]*`|-d\s+@(?:[^\s]*(?:\.env|id_rsa|\.key|\.pem))|-F\s+[^=]*=@(?:[^\s]*(?:\.env|id_rsa|\.key|\.pem))|--post-file[=\s]+(?:[^\s]*(?:\.env|id_rsa|\.key|\.pem)))""",
            cmd,
            re.IGNORECASE
        )
        if exfil_pattern:
            return GuardResult(
                blocked=True,
                check_name="DATA_EXFILTRATION",
                reason=(
                    f"Blocked potential credential exfiltration via network command: '{cmd}'. "
                    f"Piping or posting local sensitive files (.env / private keys) to remote endpoints is prohibited."
                )
            )

        # 4. Package Typosquatting / Hallucination Early Warning (Non-blocking warning)
        pkg_install_match = re.search(
            r"""\b(?:pip(?:3)?\s+install|npm\s+(?:i|install)|pnpm\s+add|yarn\s+add|bun\s+add)\s+([a-zA-Z0-9_\-\s@/]+)""",
            cmd
        )
        if pkg_install_match:
            pkg_str = pkg_install_match.group(1)
            tokens = [t.strip().lower() for t in pkg_str.split() if t.strip() and not t.startswith("-")]
            for tok in tokens:
                if tok.startswith("@"):
                    # Scoped package e.g. @org/pkg or @org/pkg@1.0.0
                    sub = tok[1:]
                    base_pkg = sub.split("@")[0].strip()
                else:
                    base_pkg = tok.split("==")[0].split("@")[0].strip()

                if base_pkg in cls.KNOWN_TYPOSQUATS or f"@{base_pkg}" in cls.KNOWN_TYPOSQUATS:
                    return GuardResult(
                        blocked=False,
                        warning_only=True,
                        check_name="PACKAGE_TYPOSQUAT_WARNING",
                        reason=(
                            f"Package '{tok}' matches a known malicious typosquatting package. "
                            f"Verify package legitimacy before installing."
                        )
                    )

        # 5. Raw Code Search Interception (Enforce TokenSafe MCP tools over terminal grep/find)
        code_search_err = cls._check_raw_code_search(cmd)
        if code_search_err:
            return GuardResult(
                blocked=True,
                check_name="RAW_CODE_SEARCH",
                reason=code_search_err
            )

        return GuardResult(blocked=False)

    @classmethod
    def _check_raw_code_search(cls, cmd: str) -> Optional[str]:
        """
        Detects unconstrained terminal code searches (grep/rg/find targeting source files).
        Enforces use of TokenSafe MCP tools (smart_read_file, get_architecture_graph).
        Excludes safe pipe filters (e.g., git log | grep, ps | grep, npm test | grep).
        """
        # Split compound commands (&&, ||, ;)
        stages = [s.strip() for s in re.split(r'(?:&&|\|\||;)', cmd) if s.strip()]

        for stage in stages:
            pipe_parts = [p.strip() for p in stage.split('|')]

            # If grep is preceded by a non-code command (ps, git log, docker, npm test), it's a pipe filter
            if len(pipe_parts) > 1:
                first_part = pipe_parts[0]
                is_file_dump = bool(re.search(r'\b(cat|less|more)\s+[\w\.\/\-]+', first_part))
                if not is_file_dump:
                    continue

            sub_cmd = pipe_parts[-1]

            # 1. Check for grep / egrep / ripgrep (rg)
            grep_match = re.search(r'\b(grep|egrep|rg|ripgrep)\b', sub_cmd)
            if grep_match:
                has_code_ext = any(ext in sub_cmd for ext in cls.CODE_EXTENSIONS)
                has_code_dir = any(re.search(rf'\b{re.escape(d)}(?:/|\b)', sub_cmd) for d in cls.CODE_DIRS)
                is_recursive_all = bool(re.search(r'\b(grep|egrep)\b.*?\s-[a-zA-Z]*[rR]\b', sub_cmd)) or \
                                   bool(re.search(r'\brg\b', sub_cmd))

                if has_code_ext or has_code_dir or is_recursive_all:
                    return (
                        f"Raw terminal code search blocked: '{sub_cmd}'. "
                        f"TokenSafe Protocol mandates using MCP tools to prevent context window bloat:\n"
                        f"• To search keywords, symbols, or configs: call 'tokensafe:scoped_search(query, scope)'\n"
                        f"• To inspect definitions/symbols in a file: call 'tokensafe:smart_read_file(path, symbol)'\n"
                        f"• To locate God Nodes & architecture: call 'tokensafe:get_architecture_graph(project_path)'\n"
                        f"• To inspect project exports & types: call 'tokensafe:get_api_surface(project_path)'"
                    )

            # 2. Check for find on source code
            find_match = re.search(r'\bfind\b\s+([\w\.\/\-]+)', sub_cmd)
            if find_match:
                has_code_ext = any(ext in sub_cmd for ext in cls.CODE_EXTENSIONS)
                has_code_dir = any(re.search(rf'\b{re.escape(d)}(?:/|\b)', sub_cmd) for d in cls.CODE_DIRS)
                if has_code_ext or has_code_dir:
                    return (
                        f"Raw file crawl via 'find' blocked: '{sub_cmd}'. "
                        f"Use 'tokensafe:scoped_search(query)' or 'tokensafe:get_architecture_graph(project_path)' "
                        f"instead of scanning directory trees."
                    )

        return None

    @classmethod
    def _is_git_ignored(cls, file_path: str, repo_root: str) -> bool:
        """Checks if a file is covered by .gitignore in the repository."""
        gitignore_path = os.path.join(repo_root, ".gitignore")
        if not os.path.exists(gitignore_path):
            return False
        try:
            filename = os.path.basename(file_path)
            rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    clean_pattern = line.lstrip("/")
                    if fnmatch.fnmatch(filename, clean_pattern) or fnmatch.fnmatch(rel_path, clean_pattern):
                        return True
                    if clean_pattern.endswith("/") and rel_path.startswith(clean_pattern):
                        return True
        except Exception:
            pass
        return False
