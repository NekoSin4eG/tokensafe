"""
Command Sanitizer: Intercepts and rewrites shell commands in PreToolUse
to prevent context window poisoning from lockfile diffs, unconstrained logs,
and verbose test output.

Every rule operates per-stage: compound commands (a && b ; c | d) are split
quote-aware, each stage is optimized independently, and the result is rejoined
so rewrites never leak pathspecs into an unrelated stage (e.g. lockfile
excludes landing on `npm test` after `git diff && npm test`).
"""

import json
import os
import re
import shlex
from typing import Tuple, Dict, Any, List, Optional

class CommandSanitizer:
    """Optimizes terminal command lines to eliminate token waste."""

    # Test-runner guard: spots jest/vitest/package-manager test stages so they
    # can be forced into run-once mode (a watch-mode command never exits, and
    # an agent blocked on its completion waits forever)
    _TOKENSAFE_TESTS_RE = re.compile(r"\btokensafe\s+tests?\b")
    _VITEST_RE = re.compile(r"^\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:sudo\s+)?(?:npx\s+|yarn\s+|pnpm\s+|bunx\s+)?vitest(?:\s|$)")
    _JEST_RE = re.compile(r"^\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:sudo\s+)?(?:npx\s+|yarn\s+|pnpm\s+|bunx\s+)?jest(?:\s|$)")
    _JEST_SCRIPT_RE = re.compile(r"\b(?:react-scripts|craco)\s+test\b")
    # Jest-only flags positively fingerprint jest behind an opaque test script
    _JEST_FINGERPRINT_RE = re.compile(r"--watchAll|--detectOpenHandles|--testPathPattern|--testMatch|--runInBand")
    _BARE_WATCH_RE = re.compile(r"\s--watch(?=[\s]|$)")

    # Lockfiles and binary patterns that should never be dumped in git diffs or searches
    LOCKFILE_EXCLUDES = [
        "':!package-lock.json'",
        "':!pnpm-lock.yaml'",
        "':!yarn.lock'",
        "':!bun.lock'",
        "':!bun.lockb'",
        "':!Cargo.lock'",
        "':!go.sum'",
        "':!composer.lock'",
        "':!*.lock'",
        "':!*.min.js'",
        "':!*.min.css'",
        "':!*.bundle.js'",
        "':!*.map'",
    ]

    # Files whose `cat` dumps thousands of near-useless tokens into context
    _CAT_JUNK_RE = re.compile(
        r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|bun\.lock|bun\.lockb"
        r"|Cargo\.lock|go\.sum|composer\.lock|poetry\.lock|Gemfile\.lock"
        r"|.*\.min\.js|.*\.min\.css|.*\.bundle\.js|.*\.map)$"
    )

    # Output limiters at command position that make an extra `| head` redundant
    _LIMITER_RE = re.compile(
        r"^(?:[A-Za-z_]\w*=\S*\s+)*(?:sudo\s+|env\s+)?(?:head|tail|less|wc|jq)\b"
    )
    _GIT_REF_RE = re.compile(
        r"^(?:HEAD(?:[~^]\d*)*|main|master|origin/[\w./-]+|[0-9a-f]{7,40}|[\w./-]+(?:\.\.\.?|@\{)[\w./@-]*|v?\d+\.\d+(?:\.\d+)*)$"
    )
    _FD_DUP_RE = re.compile(r"(?:^|\s)[0-9]*>&[0-9-]+(?=\s|$)")
    _FILE_REDIRECT_RE = re.compile(r"(?:^|\s)(?:[0-9]*>{1,2}|&>{1,2})")
    _PRECOMPILED_QUOTED_RE = re.compile(r"""'(?:[^']*)'|"(?:\\.|[^"\\])*"|`(?:[^`]*)`""")
    _ENV_PREFIX_RE = re.compile(r"""^\s*(?:[A-Za-z_]\w*=(?:"(?:\\.|[^"\\])*"|'[^']*'|\S*)\s+|sudo\s+|env\s+)*""")
    _SUBSHELL_WRAP_RE = re.compile(r"^\s*[\(\{].*[\)\}]\s*$")
    _GIT_DIFF_LIMIT_RE = re.compile(r"(?:\s-[0-9]+|\s-n\s*\d+|\s--max-count[=\s]\d+)")
    _GIT_SHOW_SHORT_RE = re.compile(r"--stat|--name-only|--name-status|\s-s\b|--oneline")
    _CURL_FILE_RE = re.compile(r"-o\s|-O\b|--output|-F\b|--form|-T\b|--upload-file|-I\b|--head")
    _LS_VERBOSE_RE = re.compile(r"\bls\s+-[lah1pF]*(?:l|a)[lah1pF]*(?=\s|$)")
    _PKG_MANAGER_TEST_RE = re.compile(r"\b(?:yarn|npm|pnpm|bun)\s+(?:run\s+)?test(?:[:_-][\w.-]+)?\b")
    _NPM_TEST_RE = re.compile(r"\bnpm\s+(?:run\s+)?test[\w:./-]*")
    _CD_RE = re.compile(r'^\s*(?:cd|pushd)\s+(?:--\s+)?(["\']?)(.+?)\1\s*$')

    # (unit, limit) specs for end-of-command output caps
    _CAP_LINES_30 = ("n", 30)
    _CAP_LINES_200 = ("n", 200)
    _CAP_BYTES_4K = ("c", 4096)

    @staticmethod
    def split_stages(cmd: str) -> List[Tuple[str, str]]:
        """
        Quote-, escape-, and subshell-aware split of a shell command into
        (operator, stage) pairs. Operators: '', '|', '||', '&&', ';', '\\n'.
        """
        pairs: List[Tuple[str, str]] = []
        buf: List[str] = []
        quote = ""
        paren_depth = 0
        brace_depth = 0
        pending_op = ""
        i = 0
        n = len(cmd)

        def flush():
            stage = "".join(buf).strip()
            buf.clear()
            pairs.append((pending_op, stage))

        while i < n:
            ch = cmd[i]
            if quote:
                # In POSIX shells, backslash only escapes inside "..." (not inside '...')
                if ch == "\\" and quote == '"' and i + 1 < n:
                    buf.append(ch)
                    buf.append(cmd[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
                buf.append(ch)
                i += 1
                continue

            if ch == "\\" and i + 1 < n:
                buf.append(ch)
                buf.append(cmd[i + 1])
                i += 2
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if ch == "(":
                paren_depth += 1
                buf.append(ch)
                i += 1
                continue
            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                buf.append(ch)
                i += 1
                continue
            if ch == "{" and (i == 0 or cmd[i - 1].isspace()):
                brace_depth += 1
                buf.append(ch)
                i += 1
                continue
            if ch == "}" and brace_depth > 0:
                brace_depth -= 1
                buf.append(ch)
                i += 1
                continue

            if paren_depth == 0 and brace_depth == 0:
                if ch == "&" and i + 1 < n and cmd[i + 1] == "&":
                    flush()
                    pending_op = "&&"
                    i += 2
                    continue
                if ch == "|" and i + 1 < n and cmd[i + 1] == "|":
                    flush()
                    pending_op = "||"
                    i += 2
                    continue
                if ch == "|":
                    flush()
                    pending_op = "|"
                    i += 1
                    continue
                if ch in (";", "\n"):
                    flush()
                    pending_op = ch
                    i += 1
                    continue

            buf.append(ch)
            i += 1

        flush()
        cleaned = [(op, s) for op, s in pairs if s]
        if not cleaned:
            cleaned = [("", "")]
        return cleaned

    @staticmethod
    def join_stages(pairs: List[Tuple[str, str]]) -> str:
        """Rejoins (operator, stage) pairs from split_stages into a command line."""
        out = ""
        for op, stage in pairs:
            if not out:
                out = stage
            elif op == "\n":
                out += f"\n{stage}"
            elif op == "|":
                out += f" | {stage}"
            else:
                out += f" {op} {stage}"
        return out

    @classmethod
    def _has_redirect(cls, stage: str) -> bool:
        """True if the stage redirects stdout to a file/stream (ignoring 2>&1 merges and quoted '>')."""
        unquoted = cls._PRECOMPILED_QUOTED_RE.sub(" ", stage)
        without_fd_dups = cls._FD_DUP_RE.sub(" ", unquoted)
        return bool(cls._FILE_REDIRECT_RE.search(without_fd_dups))

    @classmethod
    def _strip_cmd_prefix(cls, stage: str) -> str:
        return cls._ENV_PREFIX_RE.sub("", stage.lstrip())

    @classmethod
    def _split_tokens_safe(cls, stage: str) -> List[str]:
        try:
            return shlex.split(stage)
        except ValueError:
            return stage.split()

    @classmethod
    def _primary_cmd_and_args(cls, stage: str) -> Tuple[str, List[str]]:
        tokens = cls._split_tokens_safe(stage)
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            if tok in ("sudo", "env", "command", "time", "nohup"):
                idx += 1
                continue
            if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].replace("_", "").isalnum():
                idx += 1
                continue
            break
        if idx >= len(tokens):
            return "", []
        cmd_tok = os.path.basename(tokens[idx])
        return cmd_tok, tokens[idx + 1 :]

    @classmethod
    def _optimize_stage(cls, stage: str, cwd: Optional[str] = None) -> Optional[Tuple[str, int, str, Optional[Tuple[str, int]]]]:
        """
        Applies one rewrite rule to a single stage (command-position aware).
        Returns (new_stage, est_saved, reason, cap_spec) or None.
        """
        if cls._SUBSHELL_WRAP_RE.match(stage):
            return None

        cmd_tok, cmd_args = cls._primary_cmd_and_args(stage)
        if not cmd_tok:
            return None

        # git subcommands (skip optional flags like -C <dir>, -c k=v, --git-dir=...)
        if cmd_tok == "git":
            sub_idx = 0
            while sub_idx < len(cmd_args):
                a = cmd_args[sub_idx]
                if a in ("-C", "-c", "--git-dir", "--work-tree"):
                    sub_idx += 2
                elif a.startswith("-"):
                    sub_idx += 1
                else:
                    break
            subcmd = cmd_args[sub_idx] if sub_idx < len(cmd_args) else ""
            sub_args = cmd_args[sub_idx + 1 :]

            # git status: compact porcelain format + bounded output (a long-lived
            # monorepo branch can list hundreds of untracked files)
            if subcmd == "status":
                if "--porcelain" not in sub_args and "-s" not in sub_args and "-uno" not in sub_args and "--untracked-files" not in stage:
                    new_stage = re.sub(r"\b(git(?:\s+-C\s+\S+)?\s+status)\b", r"\1 --porcelain", stage, count=1)
                    return new_stage, 1500, "Switched git status to compact porcelain output, capped to 200 lines", cls._CAP_LINES_200

            # git diff: exclude lockfiles/bundles when diffing unscoped repository
            if subcmd == "diff":
                if not any(a.startswith(":!") for a in sub_args) and ":!" not in stage:
                    has_path = any(not a.startswith("-") and not cls._GIT_REF_RE.match(a) for a in sub_args)
                    if not has_path:
                        excludes_str = " ".join(cls.LOCKFILE_EXCLUDES)
                        if " -- " in stage or stage.rstrip().endswith(" --"):
                            new_stage = f"{stage.rstrip()} {excludes_str}"
                        else:
                            new_stage = f"{stage.rstrip()} -- {excludes_str}"
                        return new_stage, 3500, "Excluded lockfiles/bundles from git diff", None

            # git log: cap unbounded commit history
            elif subcmd == "log":
                has_limit = bool(cls._GIT_DIFF_LIMIT_RE.search(stage))
                if not has_limit:
                    if "--oneline" not in sub_args:
                        new_stage = re.sub(r"\b(git(?:\s+-C\s+\S+)?\s+log)\b", r"\1 -n 10 --oneline", stage, count=1)
                    else:
                        new_stage = re.sub(r"\b(git(?:\s+-C\s+\S+)?\s+log)\b", r"\1 -n 10", stage, count=1)
                    return new_stage, 1200, "Capped unbounded git log to 10 entries", None

            # git grep: exclude lockfiles & generated dirs via pathspecs
            elif subcmd == "grep":
                if ":!*.lock" not in stage and ":!bun.lock" not in stage:
                    git_excludes = "':!*.lock' ':!bun.lock' ':!bun.lockb' ':!package-lock.json' ':!dist' ':!build' ':!node_modules'"
                    if " -- " in stage or stage.rstrip().endswith(" --"):
                        new_stage = f"{stage.rstrip()} {git_excludes}"
                    else:
                        new_stage = f"{stage.rstrip()} -- . {git_excludes}"
                    return new_stage, 3500, "Excluded lockfiles from git grep and capped output", cls._CAP_LINES_30

            # git show: full patch dumps are capped
            elif subcmd == "show":
                if not cls._GIT_SHOW_SHORT_RE.search(stage):
                    return stage, 1500, "Capped git show patch output", cls._CAP_LINES_200

            # git blame: one output line per file line
            elif subcmd == "blame":
                if "-L" not in sub_args and not any(a.startswith("-L") for a in sub_args):
                    return stage, 1500, "Capped git blame output", cls._CAP_LINES_200

        # pytest (or python -m pytest): compact quiet traceback
        if cmd_tok == "pytest" or (cmd_tok.startswith("python") and "-m" in cmd_args and "pytest" in cmd_args):
            if "--tb=" not in stage and "-q" not in cmd_args and "--quiet" not in cmd_args:
                return f"{stage} -q --tb=short", 800, "Applied short traceback to pytest", None

        # cargo test: quiet test runner by default when not specified
        if cmd_tok == "cargo" and cmd_args[:1] == ["test"]:
            if "--quiet" not in stage and "-q" not in stage and "--" not in stage:
                return f"{stage} -- --quiet", 600, "Applied quiet mode to cargo test", None

        # test runners: agents block on watch mode
        guard = cls._optimize_test_stage(stage, cwd)
        if guard:
            return guard

        # find: prune unbounded recursion on simple directory crawls (avoid breaking -o / -exec / -delete expressions)
        if cmd_tok == "find" and "node_modules" not in stage and ".git" not in stage:
            has_complex = any(a in ("-o", "-or", "-exec", "-execdir", "-delete", "(") for a in cmd_args)
            if not has_complex and (
                re.search(r"\bfind\s+(\.|\/|[\w./-]+)\s*$", stage)
                or re.search(r"\bfind\s+\.\s+-name\b", stage)
            ):
                new_stage = f"{stage} -not -path '*/.*' -not -path '*/node_modules/*' -not -path '*/venv/*'"
                return new_stage, 1500, "Pruned .git, node_modules, and venv from find", None

        # grep / egrep recursive search: exclude junk dirs and cap output
        if cmd_tok in ("grep", "egrep"):
            is_recursive = any(
                (a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)) or a == "--recursive"
                for a in cmd_args
            )
            if is_recursive and "--exclude-dir" not in stage and "node_modules" not in stage:
                excludes = "--exclude='*.lock' --exclude='*.lockb' --exclude-dir={dist,build,node_modules,.git,coverage,__pycache__,.next,data}"
                return f"{stage} {excludes}", 2500, "Sanitized grep directories and capped output to 30 matches", cls._CAP_LINES_30

        # ripgrep: cap output
        if cmd_tok == "rg" and "-- " not in cmd_args and not any(a in ("-m", "--max-count", "-g", "--glob") or a.startswith(("-m", "--max-count=", "-g", "--glob=")) for a in cmd_args):
            new_stage = stage
            if "--no-ignore" in cmd_args:
                new_stage += " -g '!node_modules' -g '!.git' -g '!dist' -g '!build'"
            return new_stage, 2500, "Capped rg output to 30 matches and excluded junk dirs", cls._CAP_LINES_30

        # docker logs / docker container logs
        if cmd_tok == "docker" and (cmd_args[:1] == ["logs"] or cmd_args[:2] == ["container", "logs"]):
            if "--tail" not in stage and not any(a == "-n" or a.startswith(("-n", "--tail=")) for a in cmd_args):
                return f"{stage} --tail 100", 2000, "Capped docker logs to last 100 lines", None

        # docker build / docker compose build: the failing step sits at the end
        # of the log — keep only the tail instead of a full build dump
        if cmd_tok == "docker" and (cmd_args[:1] == ["build"] or cmd_args[:2] == ["compose", "build"]):
            if not cls._has_redirect(stage) and "|" not in stage:
                return f"{stage} 2>&1 | tail -50", 2500, "Tailed docker build output to the last 50 lines (failing step is last)", None

        # kubectl logs
        if cmd_tok == "kubectl" and cmd_args[:1] == ["logs"]:
            if "--tail" not in stage:
                return f"{stage} --tail=100", 2000, "Capped kubectl logs to last 100 lines", None

        # npm install/i/ci/add
        if cmd_tok == "npm" and cmd_args and cmd_args[0] in ("install", "i", "ci", "add"):
            if "--no-audit" not in cmd_args:
                return f"{stage} --no-progress --no-fund --no-audit", 600, "Silenced npm install progress/audit output", None

        # pip / pip3 install
        if cmd_tok in ("pip", "pip3") and cmd_args[:1] == ["install"]:
            if "-q" not in cmd_args and "--quiet" not in cmd_args:
                return f"{stage} -q", 400, "Applied quiet mode to pip install", None

        # cat on lockfiles/minified bundles: only rewrite when all paths are junk so normal files are never truncated
        if cmd_tok == "cat" and not cls._has_redirect(stage):
            if cmd_args and all(not p.startswith("-") for p in cmd_args):
                if all(cls._CAT_JUNK_RE.search(p) for p in cmd_args):
                    quoted_paths = " ".join(shlex.quote(p) for p in cmd_args)
                    return f"head -n 120 {quoted_paths}", 3000, "Replaced cat of lockfile/bundle with bounded head", None

        # curl printing to stdout
        if cmd_tok == "curl":
            if not cls._CURL_FILE_RE.search(stage):
                return stage, 3000, "Capped curl stdout to 4096 bytes", cls._CAP_BYTES_4K

        # ls: replace verbose flags (-l, -a, -la, -lah) while preserving functional flags like -R, -t, -S
        if cmd_tok == "ls" and cls._LS_VERBOSE_RE.search(stage):
            new_stage = cls._LS_VERBOSE_RE.sub("ls -1 -p", stage, count=1)
            return new_stage, 380, "Replaced verbose ls flags with compact, secret-safe format (-1 -p)", None

        return None

    @classmethod
    def _optimize_test_stage(cls, stage: str, cwd: Optional[str] = None) -> Optional[Tuple[str, int, str, None]]:
        """
        Forces run-once mode on JS test-runner stages. Agents launch these in
        the background and block until process exit; watch mode or an unclosed
        jest handle ("Jest did not exit one second after the test run has
        completed") means the process never terminates and the agent waits
        forever. The guard:
          - normalizes bare --watch / --watchAll to =false for jest-family,
          - rewrites bare `vitest` to `vitest run` (--watch=false is unreliable),
          - appends --forceExit for jest-family so open handles cannot stall exit,
          - inserts ` -- ` after `npm test` so args reach the runner (npm
            silently swallows them otherwise, leaving default watch mode on),
          - prepends CI=true as a run-once fallback for opaque package scripts
            (CRA and vitest both gate watch mode on the CI variable).
        An opaque `yarn/npm test` script is classified via a jest-only flag
        fingerprint (--watchAll, --detectOpenHandles...) when present; without
        one, `cwd` unlocks a package.json peek at scripts.test ("react-scripts
        test" -> script-jest, "vitest" -> script-vitest). No cwd or an
        unrecognized script falls back to the conservative generic treatment.
        """
        if cls._TOKENSAFE_TESTS_RE.search(stage):
            return None
        cmd_tok, cmd_args = cls._primary_cmd_and_args(stage)
        # Do not rewrite `vitest bench` or `vitest typecheck`
        vitest_sub = cmd_args[1:2] if cmd_tok in ("npx", "yarn", "pnpm", "bunx") and cmd_args[:1] == ["vitest"] else cmd_args[:1]
        if (cmd_tok == "vitest" or cls._VITEST_RE.match(stage)) and not any(a in ("bench", "typecheck") for a in vitest_sub):
            runner = "vitest"
        elif cls._JEST_RE.match(stage) or cls._JEST_SCRIPT_RE.search(stage):
            runner = "jest"
        elif cls._PKG_MANAGER_TEST_RE.match(cls._strip_cmd_prefix(stage)):
            if cls._JEST_FINGERPRINT_RE.search(stage):
                runner = "jest"
            else:
                script_runner = cls._peek_test_script_runner(cwd)
                runner = {"jest": "script-jest", "vitest": "script-vitest"}.get(script_runner, "generic")
        else:
            return None

        new_stage = stage
        reasons: List[str] = []
        saved = 0

        if runner in ("vitest", "script-vitest"):
            if cls._BARE_WATCH_RE.search(new_stage):
                new_stage = cls._BARE_WATCH_RE.sub("", new_stage)
                reasons.append("Dropped bare --watch from vitest command")
                saved += 300
            if (
                runner == "vitest"
                and not re.search(r"\bvitest\s+(?:run|bench|typecheck)\b", new_stage)
                and "--run" not in cmd_args
            ):
                # Replace vitest at command position, never inside an env-var value
                new_stage = re.sub(r"^(\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:sudo\s+)?(?:npx\s+|yarn\s+|pnpm\s+|bunx\s+)?)vitest\b", r"\1vitest run", new_stage, count=1)
                reasons.append("Rewrote vitest to one-shot `vitest run`")
                saved += 400
        elif runner in ("jest", "script-jest"):
            if cls._BARE_WATCH_RE.search(new_stage):
                new_stage = cls._BARE_WATCH_RE.sub(" --watch=false", new_stage)
                reasons.append("Normalized bare --watch to --watch=false")
                saved += 400
            if re.search(r"\s--watchAll(?=[\s]|$)", new_stage):
                new_stage = re.sub(r"\s--watchAll(?=[\s]|$)", " --watchAll=false", new_stage)
                reasons.append("Normalized bare --watchAll to --watchAll=false")
                saved += 400
            if runner == "script-jest" and "--watch=false" not in new_stage:
                # A script like "jest --watch" can bake watch mode in; jest's
                # yargs parser takes the last flag, so this one wins over it
                new_stage += " --watch=false"
                reasons.append("Appended --watch=false (package.json script may bake watch mode in)")
                saved += 300
            if "--forceExit" not in new_stage:
                new_stage += " --forceExit"
                reasons.append("Appended --forceExit so jest exits despite open handles")
                saved += 800

        # npm silently swallows args without ` -- `: insert ` -- ` unless caller is
        # passing npm's own flags (--workspace, --prefix, --silent, -w, --if-present).
        npm_match = cls._NPM_TEST_RE.search(new_stage)
        if npm_match and " -- " not in new_stage:
            tail = new_stage[npm_match.end():].lstrip()
            if tail and not tail.startswith(("--workspace", "-w ", "-w=", "--prefix", "--silent", "--if-present")):
                new_stage = new_stage[:npm_match.end()] + " -- " + tail
                reasons.append("Inserted ` -- ` so npm forwards args to the test runner")
                saved += 600

        if not re.search(r"\bCI=", new_stage):
            new_stage = f"CI=true {new_stage}"
            saved += 300
            if runner == "generic":
                reasons.append("Prepended CI=true so an unknown test runner stays in run-once mode")
            else:
                reasons.append("Prepended CI=true for run-once mode")

        if not reasons:
            return None
        return new_stage, saved, "; ".join(reasons), None

    @classmethod
    def _peek_test_script_runner(cls, cwd: Optional[str]) -> Optional[str]:
        """
        Classifies the runner behind an opaque package.json test script
        (e.g. "test": "react-scripts test" -> "jest"). Returns "jest",
        "vitest", or None when cwd is unknown, package.json is missing or
        unreadable, or the script is an unrecognized runner — callers then
        fall back to the conservative generic treatment.
        """
        if not cwd:
            return None
        try:
            with open(os.path.join(cwd, "package.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        scripts = data.get("scripts")
        script = scripts.get("test") if isinstance(scripts, dict) else None
        if not isinstance(script, str):
            return None
        if re.search(r"\bvitest\b", script):
            return "vitest"
        if re.search(r"\bjest\b", script) or cls._JEST_SCRIPT_RE.search(script):
            return "jest"
        return None

    @classmethod
    def sanitize(cls, command: str, cwd: Optional[str] = None) -> Tuple[Optional[str], int, str]:
        """
        Analyzes a shell command stage by stage. If any stage creates massive
        token waste, returns (rewritten_command, est_tokens_saved, reason).
        If no change is needed, returns (None, 0, "").
        `cwd` is the working directory the command will run in; when provided,
        the test-runner guard may read <cwd>/package.json to identify the
        runner behind an opaque test script. A `cd` stage updates that
        directory for the stages that follow (cd app && yarn test ...).
        """
        if not command or not command.strip():
            return None, 0, ""

        pairs = cls.split_stages(command.strip())
        string_changed = False
        total_saved = 0
        reasons: List[str] = []
        cap_spec: Optional[Tuple[str, int]] = None
        current_dir = os.path.abspath(os.path.expanduser(cwd)) if cwd else None

        for i, (op, stage) in enumerate(pairs):
            cd_match = cls._CD_RE.match(stage)
            if cd_match:
                target = os.path.expanduser(cd_match.group(2).strip())
                if target and target != "-":
                    base = current_dir or os.getcwd()
                    current_dir = os.path.normpath(
                        target if os.path.isabs(target) else os.path.join(base, target)
                    )
            res = cls._optimize_stage(stage, current_dir)
            if not res:
                continue
            new_stage, saved, reason, rule_cap = res
            if new_stage != stage:
                pairs[i] = (op, new_stage)
                string_changed = True
            total_saved += saved
            reasons.append(reason)
            # Only the FINAL stage may request the end-of-command cap: an
            # earlier stage's cap would land after unrelated trailing stages
            # (e.g. capping `npm test` instead of the `git grep` that fed it).
            if rule_cap and i == len(pairs) - 1:
                cap_spec = rule_cap

        cap_applied = False
        if cap_spec:
            last_stage = pairs[-1][1]
            if not cls._LIMITER_RE.match(last_stage) and not cls._has_redirect(last_stage):
                unit, limit = cap_spec
                pairs[-1] = (pairs[-1][0], f"{last_stage} | head -{unit} {limit}")
                cap_applied = True

        if not (string_changed or cap_applied):
            return None, 0, ""

        return cls.join_stages(pairs), total_saved, "; ".join(reasons)

    @classmethod
    def compact_test_output(cls, raw: str, exit_code: int = 0) -> str:
        """
        Compacts test execution output to prevent context bloating in Messages history.
        - If exit_code != 0: returns full raw output untouched to preserve error context.
        - If exit_code == 0: extracts concise summary lines (e.g. 'PASS: 42 passed in 1.2s')
          discarding repetitive passing test lines.
        """
        if exit_code != 0 or not raw or not raw.strip():
            return raw

        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return raw

        # 1. pytest check
        for line in reversed(lines):
            if re.search(r"=\s*(?:[0-9]+\s+passed.*in\s+[\d\.]+s|passed in\s+[\d\.]+s)\s*=", line):
                clean = re.sub(r"^=+\s*|\s*=+$", "", line)
                return f"PASS: {clean}"

        # 2. unittest check
        ran_line = ""
        for line in lines:
            if line.startswith("Ran ") and " test" in line:
                ran_line = line
            elif line.startswith("OK"):
                return f"PASS: {ran_line or 'all tests passed'} ({line})"

        # 3. jest / vitest check
        for line in reversed(lines):
            if "Tests:" in line and "passed" in line:
                return f"PASS: {line}"
            if re.search(r"✓.*tests? passed", line):
                return f"PASS: {line}"

        # 4. cargo test check
        for line in reversed(lines):
            if line.startswith("test result: ok."):
                return f"PASS: {line}"

        # 5. go test check
        for line in reversed(lines):
            if line.startswith("ok  \t") or line.startswith("PASS"):
                return f"PASS: {line}"

        # Fallback for verbose outputs: if > 12 lines, cap to first line + last line
        if len(lines) > 12:
            return f"PASS: {lines[0]} ... {lines[-1]} (exit code 0)"

        return raw

    # PostToolUse shell tools whose stdout can be compacted (hook payload tool_name)
    SHELL_TOOL_NAMES = {
        "Bash", "bash", "Shell", "shell", "sh",
        "run_command", "RunCommand", "execute_command", "ExecuteCommand", "terminal", "Terminal",
    }

    @staticmethod
    def _extract_tool_stdout(response: Any) -> Tuple[str, Optional[int]]:
        """Extracts (stdout_text, exit_code_or_None) from a PostToolUse tool_response payload.
        Handles plain strings, {stdout|output|content|text|result} dicts, content-block lists,
        and common exit-code keys (exit_code/exitCode/returncode/code, success booleans)."""
        if isinstance(response, str):
            return response, None
        if isinstance(response, dict):
            stdout = ""
            for key in ("stdout", "output", "content", "text", "result"):
                val = response.get(key)
                if isinstance(val, str) and val:
                    stdout = val
                    break
            if not stdout:
                blocks = response.get("content")
                if isinstance(blocks, list):
                    texts = [
                        b.get("text", "") for b in blocks
                        if isinstance(b, dict) and isinstance(b.get("text"), str)
                    ]
                    stdout = "\n".join(t for t in texts if t)
            exit_code = None
            for key in ("exit_code", "exitCode", "returncode", "return_code", "code"):
                val = response.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    exit_code = int(val)
                    break
            if exit_code is None:
                if response.get("success") is True:
                    exit_code = 0
                elif response.get("success") is False or response.get("error"):
                    exit_code = 1
            return stdout, exit_code
        return "", None

    @classmethod
    def compact_tool_output(cls, tool_name: str, tool_response: Any) -> Optional[Tuple[str, int, int]]:
        """
        PostToolUse helper: compacts verbose successful shell output before the model reads it.
        Returns (compacted_text, original_chars, compacted_chars), or None when not applicable:
        non-shell tool, short output, failed command, or when compaction saves too little.
        Fail-open by design — callers must degrade to the raw output on None.
        """
        if str(tool_name or "") not in cls.SHELL_TOOL_NAMES:
            return None
        raw, resolved_exit = cls._extract_tool_stdout(tool_response)
        if not raw or len(raw) < 400:
            return None
        if resolved_exit is None:
            # Unknown exit status: refuse to mute anything that looks like a failure
            if re.search(r"Traceback|AssertionError|\bERROR\b|FAILED|exit code [1-9]", raw):
                return None
            resolved_exit = 0
        elif resolved_exit != 0:
            return None
        compacted = cls.compact_test_output(raw, resolved_exit)
        if compacted is raw or len(compacted) >= len(raw) * 0.6:
            return None
        return compacted, len(raw), len(compacted)
