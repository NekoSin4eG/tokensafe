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
import shlex
import fnmatch
import subprocess
from typing import Tuple, Optional, Dict, Any, List, NamedTuple
from src.secret_redactor import SecretRedactor
from src.command_sanitizer import CommandSanitizer

_TOKENSAFE_SRC_DIR = os.path.dirname(os.path.abspath(__file__))

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

    _TEST_PATH_RE = re.compile(
        r"(?:^|/)(?:__tests__|__mocks__|tests?|specs?|mocks?|fixtures?)(?:/|$)"
        r"|(?:^|/)(?:test_[^/]+|mock_[^/]+|[^/]+_test\.[^/]+|[^/]+_spec\.[^/]+|[^/]+\.test\.[^/]+|[^/]+\.spec\.[^/]+|conftest\.py)$",
        re.IGNORECASE,
    )
    _CODE_DIR_RE = re.compile(
        r"(?:^|[\s/'\"=])(?:" + "|".join(CODE_DIRS) + r")(?:/|$|[\s'\"])"
    )
    _TEMPLATE_EXAMPLES = (".example", ".sample", ".template")

    @classmethod
    def is_doc_file(cls, file_path: str) -> bool:
        """Determines if the targeted file is documentation or non-executable text."""
        if not file_path:
            return False
        return file_path.lower().endswith((".md", ".markdown", ".rst", ".txt", ".json", ".yaml", ".yml"))

    @classmethod
    def is_test_file(cls, file_path: str) -> bool:
        """Determines if the targeted file is part of a test suite or mock fixture using segment/filename boundaries."""
        if not file_path:
            return False
        norm = file_path.replace("\\", "/")
        return bool(cls._TEST_PATH_RE.search(norm))

    @classmethod
    def is_scanner_file(cls, file_path: str) -> bool:
        """True only for TokenSafe's own guard/redactor modules (never arbitrary app/guard.py files)."""
        if not file_path:
            return False
        abs_p = os.path.abspath(file_path)
        return abs_p in (
            os.path.join(_TOKENSAFE_SRC_DIR, "guard.py"),
            os.path.join(_TOKENSAFE_SRC_DIR, "secret_redactor.py"),
        )

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
        is_scanner = cls.is_scanner_file(file_path)
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
            # Python: verify=False (requests, httpx, urllib3) or ssl=False / unverified context
            if re.search(r"""(?:\bverify\s*=\s*False\b|\bssl\s*=\s*False\b|ssl\._create_unverified_context\s*\()""", code_snippet):
                return GuardResult(
                    blocked=True,
                    check_name="TLS_VERIFY_BYPASS",
                    reason=(
                        f"Insecure SSL/TLS verification bypass detected in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Disabling TLS certificate checks exposes connections to Man-in-the-Middle (MitM) attacks."
                    )
                )

            # Node.js: NODE_TLS_REJECT_UNAUTHORIZED = '0' or rejectUnauthorized: false
            if re.search(r"""(?:NODE_TLS_REJECT_UNAUTHORIZED(?:\s*["'\]]?\s*=\s*|["']?\s*:\s*)['"]0['"]|\b["']?rejectUnauthorized["']?\s*:\s*false\b)""", code_snippet):
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
            _db_clients = r"(?:cursor|cur|conn|connection|db|pool|client|knex|session|sequelize|prisma)"
            _sql_kw = r"(?:\s*SELECT|\s*INSERT|\s*UPDATE|\s*DELETE|\s*DROP|\s*ALTER|\s*TRUNCATE)"
            # Python: f-string or + concatenation inside .execute()
            py_fstring_sql = re.search(
                rf"""\b{_db_clients}\.execute\s*\(\s*(?:f["']{_sql_kw}|["']{_sql_kw}[^"']*["']\s*\+)""",
                code_snippet,
                re.IGNORECASE
            )
            if py_fstring_sql:
                return GuardResult(
                    blocked=True,
                    check_name="SQL_INJECTION",
                    reason=(
                        f"Potential SQL injection detected: dynamic string interpolation inside query execution in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Use parameterized queries with bound placeholders ('?' or '%s') instead of dynamic string formatting."
                    )
                )

            # Python: .execute("... %s ..." % (...)) or .format(...)
            py_format_sql = re.search(
                rf"""\b{_db_clients}\.execute\s*\(\s*["']{_sql_kw}[^"']+['"]\s*(?:%|\.format\s*\()""",
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

            # JS/TS: db.query(`SELECT ... ${var}`) or raw `+ var`
            js_template_sql = re.search(
                rf"""\b{_db_clients}\.(?:query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\s*\(\s*(?:`{_sql_kw}[^`]*\$\{{[^}}]+\}}|["']{_sql_kw}[^"']*["']\s*\+)""",
                code_snippet,
                re.IGNORECASE
            )
            if js_template_sql:
                return GuardResult(
                    blocked=True,
                    check_name="SQL_INJECTION",
                    reason=(
                        f"Potential SQL injection detected: template literal or concatenation inside query execution in "
                        f"'{os.path.basename(file_path) if file_path else 'code'}'. "
                        f"Use parameterized queries with bound placeholders ('$1', '$2' or '?') instead of string interpolation."
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
    def _tokenize_stage(cls, stage: str) -> List[str]:
        try:
            return shlex.split(stage)
        except ValueError:
            return stage.split()

    @classmethod
    def _stage_cmd_tokens(cls, stage: str) -> Tuple[str, List[str]]:
        tokens = cls._tokenize_stage(stage)
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("sudo", "env", "command", "time", "nohup"):
                i += 1
                continue
            if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].replace("_", "").isalnum():
                i += 1
                continue
            break
        if i >= len(tokens):
            return "", []
        return os.path.basename(tokens[i]), tokens[i + 1 :]

    @classmethod
    def _is_sensitive_filename(cls, path_arg: str) -> bool:
        base = os.path.basename(path_arg.rstrip("/\\"))
        if not base:
            return False
        low = base.lower()
        if any(low.endswith(suf) for suf in cls._TEMPLATE_EXAMPLES):
            return False
        if low in cls.SENSITIVE_GIT_FILES:
            return True
        if low.startswith(".env."):
            return True
        if low.endswith((".pem", ".key")):
            return True
        return False

    @classmethod
    def inspect_command(cls, command: str, cwd: str = "") -> GuardResult:
        """
        Audits shell and terminal commands before execution.
        Returns GuardResult with blocked status or non-blocking warnings.
        """
        if not command or not isinstance(command, str):
            return GuardResult(blocked=False)

        cmd = command.strip()
        stages = [st for _, st in CommandSanitizer.split_stages(cmd) if st]

        # 1. Destructive Unbounded File Deletion (rm -rf /, rm -rf ~, rm -rf *, etc.)
        _DANGEROUS_RM_TARGETS = {"", "/", "~", "~/", "~/*", "$HOME", "${HOME}", "..", ".", "./", "*", "/*", "./*"}
        for stage in stages:
            cmd_tok, cmd_args = cls._stage_cmd_tokens(stage)
            if cmd_tok != "rm":
                continue
            has_rf = False
            no_preserve_root = False
            targets: List[str] = []
            past_ddash = False
            for a in cmd_args:
                if a == "--" and not past_ddash:
                    past_ddash = True
                    continue
                if not past_ddash and a.startswith("-"):
                    if a == "--no-preserve-root":
                        no_preserve_root = True
                    elif a in ("--recursive", "--force"):
                        has_rf = True
                    elif not a.startswith("--") and ("r" in a or "f" in a or "R" in a):
                        has_rf = True
                else:
                    targets.append(a)
            if no_preserve_root and has_rf:
                return GuardResult(
                    blocked=True,
                    check_name="DESTRUCTIVE_RM",
                    reason=(
                        f"Destructive unbounded file deletion blocked: '{cmd}'. "
                        f"Running 'rm -rf' with --no-preserve-root poses catastrophic data loss risk."
                    )
                )
            if has_rf:
                for tgt in targets:
                    norm_tgt = tgt.rstrip("/") if tgt != "/" else "/"
                    if tgt in _DANGEROUS_RM_TARGETS or norm_tgt in ("/", "~", "$HOME", "${HOME}", ".", "..", "*"):
                        return GuardResult(
                            blocked=True,
                            check_name="DESTRUCTIVE_RM",
                            reason=(
                                f"Destructive unbounded file deletion blocked: '{cmd}'. "
                                f"Running 'rm -rf' on root, home, parent directories, or wildcards poses catastrophic data loss risk."
                            )
                        )

        # 2. Git Staging Protection for Sensitive Files (.env, private keys)
        for stage in stages:
            cmd_tok, cmd_args = cls._stage_cmd_tokens(stage)
            if cmd_tok != "git":
                continue
            git_dir = cwd or os.getcwd()
            sub_idx = 0
            while sub_idx < len(cmd_args):
                a = cmd_args[sub_idx]
                if a == "-C" and sub_idx + 1 < len(cmd_args):
                    cand_dir = cmd_args[sub_idx + 1]
                    git_dir = cand_dir if os.path.isabs(cand_dir) else os.path.abspath(os.path.join(git_dir, cand_dir))
                    sub_idx += 2
                elif a in ("-c", "--git-dir", "--work-tree") and sub_idx + 1 < len(cmd_args):
                    sub_idx += 2
                elif a.startswith("-"):
                    sub_idx += 1
                else:
                    break
            subcmd = cmd_args[sub_idx] if sub_idx < len(cmd_args) else ""
            sub_args = cmd_args[sub_idx + 1 :]

            if subcmd == "add":
                blanket = False
                for a in sub_args:
                    if a in ("--all", "-A", ".", "./", "*"):
                        blanket = True
                    elif not a.startswith("-") and cls._is_sensitive_filename(a):
                        return GuardResult(
                            blocked=True,
                            check_name="GIT_SECRET_STAGING",
                            reason=(
                                f"Blocked staging of sensitive credential file into git repository: '{cmd}'. "
                                f"Environment files and private keys must be added to .gitignore and never committed to source control."
                            )
                        )
                if blanket:
                    sensitive = cls._find_unignored_sensitive_file(git_dir)
                    if sensitive:
                        return GuardResult(
                            blocked=True,
                            check_name="GIT_SECRET_STAGING",
                            reason=(
                                f"Blocked blanket git staging: '{cmd}'. "
                                f"Unignored sensitive file '{sensitive}' detected in working tree. "
                                f"Add '{sensitive}' to .gitignore or stage files explicitly to prevent credential leaks into git."
                            )
                        )
            elif subcmd == "commit":
                if any(a == "--all" or (a.startswith("-") and not a.startswith("--") and "a" in a) for a in sub_args):
                    sensitive = cls._find_unignored_sensitive_file(git_dir)
                    if sensitive:
                        return GuardResult(
                            blocked=True,
                            check_name="GIT_SECRET_STAGING",
                            reason=(
                                f"Blocked blanket git staging: '{cmd}'. "
                                f"Unignored sensitive file '{sensitive}' detected in working tree. "
                                f"Add '{sensitive}' to .gitignore or stage files explicitly to prevent credential leaks into git."
                            )
                        )

        # 3. Network Data Exfiltration Prevention (piping or posting secrets to curl/wget/nc)
        _SENS_EXFIL = r"(?:\.env(?:\.[\w-]+)?|id_rsa|id_ed25519|\.key|\.pem|credentials\.json|service_account\.json|secret|password|token)"
        exfil_pattern = re.search(
            rf"""(?:\b(?:curl|wget|nc|ncat|netcat)\b.*?(?:\$\(\s*(?:cat\s+)?<?\s*[^)]*{_SENS_EXFIL}[^)]*\)|`cat\s+[^`]*{_SENS_EXFIL}[^`]*`|(?:-d|--data|--data-binary|--data-raw|-T|--upload-file)[=\s]*@?[^\s]*{_SENS_EXFIL}|-F\s+[^=]*=@[^\s]*{_SENS_EXFIL}|--post-file[=\s]+[^\s]*{_SENS_EXFIL}|<\s*[^\s]*{_SENS_EXFIL})|\bcat\s+[^\s|]*{_SENS_EXFIL}\s*\|\s*(?:curl|wget|nc|ncat|netcat)\b)""",
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
        for pkg_install_match in re.finditer(
            r"""\b(?:pip(?:3)?\s+install|npm\s+(?:i|install)|pnpm\s+add|yarn\s+add|bun\s+add)\s+([^;&|\n]+)""",
            cmd
        ):
            pkg_str = pkg_install_match.group(1)
            tokens = [t.strip().lower() for t in pkg_str.split() if t.strip() and not t.startswith("-")]
            for tok in tokens:
                if tok.startswith("@"):
                    sub = tok[1:]
                    base_pkg = sub.split("@")[0].strip()
                else:
                    base_pkg = re.split(r"[=<>~!@\[\]]", tok, maxsplit=1)[0].strip()

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
    def _has_code_target_in_args(cls, cmd_tok: str, cmd_args: List[str]) -> bool:
        """Separates grep/rg/find pattern vs path args so grep 'app' server.log is not flagged."""
        positionals: List[str] = []
        skip_next = False
        value_flags = {"-e", "-f", "-m", "-A", "-B", "-C", "-g", "--glob", "--type", "-t", "-name", "-iname", "-path"}
        for a in cmd_args:
            if skip_next:
                skip_next = False
                continue
            if a in value_flags:
                skip_next = True
                continue
            if a.startswith("-"):
                continue
            positionals.append(a)

        # For grep/egrep/rg without -e, the first positional is the PATTERN; remaining positionals are PATHS.
        if cmd_tok in ("grep", "egrep", "rg", "ripgrep"):
            has_e = any(a == "-e" or a.startswith("--regexp") for a in cmd_args)
            path_args = positionals if has_e else positionals[1:]
        else:
            path_args = positionals

        for p in path_args:
            low_p = p.lower()
            if any(low_p.endswith(ext) or ext in low_p for ext in cls.CODE_EXTENSIONS):
                return True
            segs = [s for s in re.split(r"[/\\]", p.strip("\"'")) if s]
            if any(s in cls.CODE_DIRS for s in segs):
                return True
        return False

    @classmethod
    def _check_raw_code_search(cls, cmd: str) -> Optional[str]:
        """
        Detects unconstrained terminal code searches (grep/rg/find targeting source files).
        Quote-aware splitting catches piped bypasses (rg foo src/ | head) while
        allowing safe process pipe filters (ps aux | grep node, cat server.log | grep app).
        """
        pairs = CommandSanitizer.split_stages(cmd)
        # Group pipeline chains separated by && / || / ; / \n
        pipelines: List[List[str]] = []
        for op, stage in pairs:
            if op == "|" and pipelines:
                pipelines[-1].append(stage)
            else:
                pipelines.append([stage])

        for pipe_parts in pipelines:
            first_tok, first_args = cls._stage_cmd_tokens(pipe_parts[0])
            targets_to_check: List[str] = []
            if len(pipe_parts) == 1:
                targets_to_check = [pipe_parts[0]]
            else:
                if first_tok in ("grep", "egrep", "rg", "ripgrep", "find") or (first_tok == "git" and "grep" in first_args[:3]):
                    targets_to_check.append(pipe_parts[0])
                if first_tok in ("cat", "less", "more"):
                    if any(any(a.lower().endswith(ext) for ext in cls.CODE_EXTENSIONS) for a in first_args):
                        targets_to_check.append(pipe_parts[-1])

            for sub_cmd in targets_to_check:
                cmd_tok, cmd_args = cls._stage_cmd_tokens(sub_cmd)
                is_git_grep = cmd_tok == "git" and "grep" in cmd_args[:3]

                if cmd_tok in ("grep", "egrep", "rg", "ripgrep") or is_git_grep:
                    effective_args = cmd_args[cmd_args.index("grep") + 1:] if is_git_grep else cmd_args
                    has_code_ext = any(ext in sub_cmd for ext in cls.CODE_EXTENSIONS)
                    has_code_dir = cls._has_code_target_in_args("grep" if is_git_grep else cmd_tok, effective_args)
                    is_recursive_grep = (cmd_tok in ("grep", "egrep") or is_git_grep) and any(
                        (a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)) or a == "--recursive"
                        for a in effective_args
                    )
                    is_rg = cmd_tok in ("rg", "ripgrep")

                    if has_code_ext or has_code_dir or is_recursive_grep or is_rg:
                        return (
                            f"Raw terminal code search blocked: '{sub_cmd}'. "
                            f"TokenSafe Protocol mandates using MCP tools to prevent context window bloat:\n"
                            f"• To search keywords, symbols, or configs: call 'tokensafe:scoped_search(query, scope)'\n"
                            f"• To inspect definitions/symbols in a file: call 'tokensafe:smart_read_file(path, symbol)'\n"
                            f"• To locate God Nodes & architecture: call 'tokensafe:get_architecture_graph(project_path)'\n"
                            f"• To inspect project exports & types: call 'tokensafe:get_api_surface(project_path)'"
                        )

                elif cmd_tok == "find":
                    has_code_ext = any(ext in sub_cmd for ext in cls.CODE_EXTENSIONS)
                    has_code_dir = cls._has_code_target_in_args(cmd_tok, cmd_args) or bool(cls._CODE_DIR_RE.search(sub_cmd))
                    is_root_file_crawl = any(a in (".", "./") for a in cmd_args[:2]) and ("-type" in cmd_args and "f" in cmd_args)
                    if has_code_ext or has_code_dir or is_root_file_crawl:
                        return (
                            f"Raw file crawl via 'find' blocked: '{sub_cmd}'. "
                            f"Use 'tokensafe:scoped_search(query)' or 'tokensafe:get_architecture_graph(project_path)' "
                            f"instead of scanning directory trees."
                        )

        return None

    @classmethod
    def _find_unignored_sensitive_file(cls, target_dir: str) -> Optional[str]:
        """
        Returns the repo-relative path of the first unignored sensitive file, or None.
        Prefers `git status --porcelain -uall` (skipping deleted 'D' entries).
        """
        def _is_sensitive_name(name: str) -> bool:
            low = name.lower()
            if any(low.endswith(suf) for suf in cls._TEMPLATE_EXAMPLES):
                return False
            return name in cls.SENSITIVE_GIT_FILES or (name.startswith(".env") and not any(low.endswith(suf) for suf in cls._TEMPLATE_EXAMPLES))

        # 1. Fast & exact: ask git itself (only lists non-ignored files).
        try:
            res = subprocess.run(
                ["git", "-C", target_dir, "status", "--porcelain", "-uall"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if len(line) >= 2 and line[:2] in (" D", "D ", "DD"):
                        continue
                    path_part = line[3:].strip() if len(line) > 3 else ""
                    if path_part.startswith('"') and path_part.endswith('"'):
                        path_part = path_part[1:-1]
                    for candidate in (seg.strip() for seg in path_part.split("->")):
                        if candidate and _is_sensitive_name(os.path.basename(candidate)):
                            return candidate
                return None
        except Exception:
            pass

        # 2. Fallback: bounded manual walk with cached .gitignore patterns
        ignore_patterns = cls._load_gitignore_patterns(target_dir)
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".turbo")]
            for f in files:
                if _is_sensitive_name(f):
                    full = os.path.join(root, f)
                    rel_path = os.path.relpath(full, target_dir)
                    if not cls._matches_gitignore(full, target_dir, ignore_patterns):
                        return rel_path
        return None

    @staticmethod
    def _load_gitignore_patterns(repo_root: str) -> List[str]:
        gitignore_path = os.path.join(repo_root, ".gitignore")
        patterns: List[str] = []
        if not os.path.exists(gitignore_path):
            return patterns
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if line and not line.startswith("#") and not line.startswith("!"):
                        patterns.append(line.lstrip("/"))
        except Exception:
            pass
        return patterns

    @staticmethod
    def _matches_gitignore(file_path: str, repo_root: str, patterns: List[str]) -> bool:
        if not patterns:
            return False
        filename = os.path.basename(file_path)
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        for clean_pattern in patterns:
            if fnmatch.fnmatch(filename, clean_pattern) or fnmatch.fnmatch(rel_path, clean_pattern):
                return True
            if clean_pattern.endswith("/") and rel_path.startswith(clean_pattern):
                return True
            if "/" not in clean_pattern and rel_path.startswith(clean_pattern + "/"):
                return True
        return False
