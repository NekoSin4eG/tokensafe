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
    _PKG_MANAGER_TEST_RE = re.compile(r"\b(?:yarn|npm|pnpm|bun)\s+(?:run\s+)?test\b")
    # Jest-only flags positively fingerprint jest behind an opaque test script
    _JEST_FINGERPRINT_RE = re.compile(r"--watchAll|--detectOpenHandles|--testPathPattern|--testMatch|--runInBand")
    _BARE_WATCH_RE = re.compile(r"\s--watch(?=[\s]|$)")
    _NPM_TEST_RE = re.compile(r"\bnpm\s+(?:run\s+)?test[\w:./-]*")
    _CD_RE = re.compile(r'^\s*cd\s+(["\']?)(.+?)\1\s*$')

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

    # Output limiters that make an extra `| head` at the end redundant
    _LIMITER_RE = re.compile(r"\b(head|tail|less|wc|jq)\b")

    # (unit, limit) specs for end-of-command output caps
    _CAP_LINES_30 = ("n", 30)
    _CAP_LINES_200 = ("n", 200)
    _CAP_BYTES_4K = ("c", 4096)

    @staticmethod
    def split_stages(cmd: str) -> List[Tuple[str, str]]:
        """
        Quote-aware split of a shell command into (operator, stage) pairs.
        Operators: '' (first stage), '|', '||', '&&', ';'. Stages are stripped;
        rejoining with join_stages() yields an equivalent command line.
        """
        pairs: List[Tuple[str, str]] = []
        buf: List[str] = []
        quote = ""
        pending_op = ""  # operator preceding the stage being collected
        i = 0
        n = len(cmd)

        def flush():
            stage = "".join(buf).strip()
            buf.clear()
            pairs.append((pending_op, stage))

        while i < n:
            ch = cmd[i]
            if quote:
                if ch == "\\" and quote != "`" and i + 1 < n:
                    buf.append(ch)
                    buf.append(cmd[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
                buf.append(ch)
            elif ch in ("'", '"', "`"):
                quote = ch
                buf.append(ch)
            elif ch == "&" and i + 1 < n and cmd[i + 1] == "&":
                flush()
                pending_op = "&&"
                i += 2
                continue
            elif ch == "|" and i + 1 < n and cmd[i + 1] == "|":
                flush()
                pending_op = "||"
                i += 2
                continue
            elif ch == "|":
                flush()
                pending_op = "|"
            elif ch == ";":
                flush()
                pending_op = ";"
            else:
                buf.append(ch)
            i += 1
        flush()
        # Drop empty stages but keep at least one slot so rejoin stays valid
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
            elif op == "|":
                out += f" | {stage}"
            else:
                out += f" {op} {stage}"
        return out

    @staticmethod
    def _split_pipeline_stages(cmd: str) -> List[str]:
        """
        Splits a shell command on single '|' pipe operators while respecting
        quoting ('...', "...", `...`) and '||' logical OR, so regex patterns like
        grep "foo|bar" are never torn apart mid-string.
        """
        pairs = CommandSanitizer.split_stages(cmd)
        # Legacy contract: only pipe boundaries split; '&&'/';'/'||' stay inside stages.
        stages: List[str] = []
        pipe_open = False
        for op, stage in pairs:
            if op in ("", "|"):
                if pipe_open and stages:
                    stages[-1] = f"{stages[-1]} | {stage}"
                else:
                    stages.append(stage)
                pipe_open = op == "|"
            else:
                # Non-pipe operator: glue operator + stage back into current stage
                if stages:
                    stages[-1] = f"{stages[-1]} {op} {stage}"
                else:
                    stages.append(stage)
        return [s for s in (st.strip() for st in stages) if s]

    @staticmethod
    def _has_redirect(stage: str) -> bool:
        """True if the stage redirects stdout somewhere (> , >>, 2>, &>)."""
        return bool(re.search(r"(^|\s)[0-9&]*>{1,2}", stage))

    @classmethod
    def _optimize_stage(cls, stage: str, cwd: Optional[str] = None) -> Optional[Tuple[str, int, str, Optional[Tuple[str, int]]]]:
        """
        Applies one rewrite rule to a single stage.
        Returns (new_stage, est_saved, reason, cap_spec) or None.
        cap_spec = (unit, limit) marks rules whose unbounded output must be
        capped; the cap is appended to the END of the whole command (never
        mid-compound), so `||` / `&&` exit-status semantics are untouched.
        new_stage may equal stage for cap-only rules (git show, blame, curl).
        """

        # git diff: exclude lockfiles/bundles when diffing unscoped repository
        if re.search(r"\bgit\s+diff\b", stage):
            if ":!package-lock" not in stage and ":!*.lock" not in stage and ":!" not in stage:
                tokens = stage.split()[2:]  # args after git diff
                has_path = any(not t.startswith('-') and not re.match(r'^(HEAD(~[0-9]+)?|main|master|[0-9a-f]{7,40})$', t) for t in tokens)
                if not has_path:
                    excludes_str = " ".join(cls.LOCKFILE_EXCLUDES)
                    if " -- " in stage:
                        new_stage = f"{stage} {excludes_str}"
                    else:
                        new_stage = f"{stage} -- {excludes_str}"
                    return new_stage, 3500, "Excluded lockfiles/bundles from git diff", None

        # git log: cap unbounded commit history
        if re.search(r"\bgit\s+log\b", stage):
            has_limit = re.search(r"(\s-[0-9]+|\s-n\s*\d+|\s--max-count[=\s]\d+)", stage)
            if not has_limit:
                if "--oneline" not in stage:
                    new_stage = re.sub(r"\bgit\s+log\b", "git log -n 10 --oneline", stage, count=1)
                else:
                    new_stage = re.sub(r"\bgit\s+log\b", "git log -n 10", stage, count=1)
                return new_stage, 1200, "Capped unbounded git log to 10 entries", None

        # pytest: compact quiet traceback
        if re.search(r"\bpytest\b", stage):
            has_tb = "--tb=" in stage or "-q" in stage
            if not has_tb:
                return f"{stage} -q --tb=short", 800, "Applied short traceback to pytest", None

        # test runners: agents block on these in the background; watch mode or
        # an unclosed jest handle means the process never exits (infinite wait)
        guard = cls._optimize_test_stage(stage, cwd)
        if guard:
            return guard

        # find: prune unbounded recursion
        if re.search(r"\bfind\s+(\.|\/|\w+)\s*$", stage) or re.search(r"\bfind\s+\.\s+-name\b", stage):
            if "node_modules" not in stage and ".git" not in stage:
                new_stage = f"{stage} -not -path '*/.*' -not -path '*/node_modules/*' -not -path '*/venv/*'"
                return new_stage, 1500, "Pruned .git, node_modules, and venv from find", None

        # git grep: exclude lockfiles & generated dirs via pathspecs
        if re.search(r"\bgit\b.*?\bgrep\b", stage):
            if ":!*.lock" not in stage and ":!bun.lock" not in stage:
                git_excludes = "':!*.lock' ':!bun.lock' ':!bun.lockb' ':!package-lock.json' ':!dist' ':!build' ':!node_modules'"
                if " -- " in stage:
                    new_stage = f"{stage} {git_excludes}"
                else:
                    new_stage = f"{stage} -- . {git_excludes}"
                return new_stage, 3500, "Excluded lockfiles from git grep and capped output", cls._CAP_LINES_30

        # grep / egrep recursive search: exclude junk dirs and cap output
        if re.search(r"\b(grep|egrep)\b", stage) and not re.search(r"\bgit\b.*?\bgrep\b", stage):
            is_recursive = bool(re.search(r"\b(grep|egrep)\b.*?\s-[a-zA-Z]*[rR]", stage))
            if is_recursive and "--exclude-dir" not in stage and "node_modules" not in stage:
                excludes = "--exclude='*.lock' --exclude='*.lockb' --exclude-dir={dist,build,node_modules,.git,coverage,__pycache__,.next,data}"
                return f"{stage} {excludes}", 2500, "Sanitized grep directories and capped output to 30 matches", cls._CAP_LINES_30

        # ripgrep: cap per-command output; add ignore excludes only for --no-ignore runs
        if re.search(r"(^|\s|\()rg\b", stage) and "-- " not in stage:
            if "-m " not in stage and "--max-count" not in stage and "-g " not in stage:
                new_stage = stage
                if "--no-ignore" in stage:
                    new_stage += " -g '!node_modules' -g '!.git' -g '!dist' -g '!build'"
                return new_stage, 2500, "Capped rg output to 30 matches and excluded junk dirs", cls._CAP_LINES_30

        # git show: full patch dumps are capped
        if re.search(r"\bgit\s+show\b", stage):
            if not re.search(r"--stat|--name-only|--name-status|\s-s\b|--oneline", stage):
                return stage, 1500, "Capped git show patch output", cls._CAP_LINES_200

        # git blame: one output line per file line — huge on big files unless -L limited
        if re.search(r"\bgit\s+blame\b", stage):
            if "-L" not in stage:
                return stage, 1500, "Capped git blame output", cls._CAP_LINES_200

        # docker logs: bound container log dumps
        if re.search(r"\bdocker\s+(container\s+)?logs\b", stage):
            if "--tail" not in stage and not re.search(r"\s-n\s*\d+", stage):
                return f"{stage} --tail 100", 2000, "Capped docker logs to last 100 lines", None

        # kubectl logs: bound pod log dumps
        if re.search(r"\bkubectl\s+logs\b", stage):
            if "--tail" not in stage:
                return f"{stage} --tail=100", 2000, "Capped kubectl logs to last 100 lines", None

        # npm installs: suppress progress bars, funding and audit noise
        if re.search(r"\bnpm\s+(install|i|ci|add)\b", stage):
            if "--no-audit" not in stage:
                return f"{stage} --no-progress --no-fund --no-audit", 600, "Silenced npm install progress/audit output", None

        # pip installs: quiet mode keeps dependency walls out of context
        if re.search(r"\bpip3?\s+install\b", stage):
            if not re.search(r"\s-q\b|--quiet", stage):
                return f"{stage} -q", 400, "Applied quiet mode to pip install", None

        # cat on lockfiles/minified bundles: switch to a bounded head window
        if re.match(r"\bcat\s", stage):
            if not cls._has_redirect(stage):
                parts = stage.split()
                paths = parts[1:]
                if paths and all(not p.startswith("-") for p in paths):
                    if any(cls._CAT_JUNK_RE.search(p) for p in paths):
                        return f"head -n 120 {' '.join(paths)}", 3000, "Replaced cat of lockfile/bundle with bounded head", None

        # curl printing to stdout (JSON/HTML dumps land in context): bounded byte cap
        if re.search(r"\bcurl\b", stage):
            if not re.search(r"-o\s|-O\b|--output|-F\b|--form|-T\b|--upload-file|-I\b|--head", stage):
                return stage, 3000, "Capped curl stdout to 4096 bytes", cls._CAP_BYTES_4K

        # ls: replace verbose permissions/timestamps with compact single-column format
        if re.search(r"\bls\b", stage):
            if re.search(r"\bls\s+-[a-zA-Z]*(l|a)[a-zA-Z]*\b", stage):
                new_stage = re.sub(r"\bls\s+-[a-zA-Z]+\b", "ls -1 -p", stage)
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
        if cls._VITEST_RE.match(stage):
            runner = "vitest"
        elif cls._JEST_RE.match(stage) or cls._JEST_SCRIPT_RE.search(stage):
            runner = "jest"
        elif cls._PKG_MANAGER_TEST_RE.search(stage):
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
            if runner == "vitest" and not re.search(r"\bvitest\s+run\b", new_stage):
                new_stage = re.sub(r"\bvitest\b", "vitest run", new_stage, count=1)
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

        # npm silently swallows args without ` -- `: in `npm test --watchAll=false`
        # the flag never reaches jest, which then stays on its default watch mode
        npm_match = cls._NPM_TEST_RE.search(new_stage)
        if npm_match and " -- " not in new_stage and new_stage[npm_match.end():].strip():
            new_stage = new_stage[:npm_match.end()] + " -- " + new_stage[npm_match.end():].lstrip()
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
            if not cls._LIMITER_RE.search(last_stage) and not cls._has_redirect(last_stage):
                unit, limit = cap_spec
                pairs[-1] = (pairs[-1][0], f"{last_stage} | head -{unit} {limit}")
                cap_applied = True

        if not (string_changed or cap_applied):
            return None, 0, ""

        return cls.join_stages(pairs), total_saved, "; ".join(reasons)
