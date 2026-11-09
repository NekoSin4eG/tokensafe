"""
Command Sanitizer: Intercepts and rewrites shell commands in PreToolUse
to prevent context window poisoning from lockfile diffs, unconstrained logs,
and verbose test output.
"""

import re
from typing import Tuple, Dict, Any, Optional

class CommandSanitizer:
    """Optimizes terminal command lines to eliminate token waste."""

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

    @classmethod
    def sanitize(cls, command: str) -> Tuple[Optional[str], int, str]:
        """
        Analyzes a shell command. If it creates massive token waste,
        returns (rewritten_command, est_tokens_saved, reason).
        If no change needed, returns (None, 0, "").
        """
        if not command or not command.strip():
            return None, 0, ""

        cmd = command.strip()
        has_compound_operators = bool(re.search(r"(&&|\|\||;)", cmd))

        # 1. git diff optimization (Exclude massive lockfiles and bundles only when diffing unscoped repository)
        if re.search(r"\bgit\s+diff\b", cmd):
            # Check if user already specified lockfile exclusion
            if ":!package-lock" not in cmd and ":!*.lock" not in cmd and ":!" not in cmd:
                tokens = cmd.split()[2:] # args after git diff
                has_path = any(not t.startswith('-') and not re.match(r'^(HEAD(~[0-9]+)?|main|master|[0-9a-f]{7,40})$', t) for t in tokens)
                if not has_path:
                    excludes_str = " ".join(cls.LOCKFILE_EXCLUDES)
                    if " -- " in cmd:
                        new_cmd = f"{cmd} {excludes_str}"
                    else:
                        new_cmd = f"{cmd} -- {excludes_str}"

                    est_saved = 3500
                    return new_cmd, est_saved, "Excluded lockfiles/bundles from git diff"

        # 2. git log optimization (Cap unbounded commit history)
        if re.search(r"\bgit\s+log\b", cmd):
            # Check if already limited by -n, -[0-9], or --max-count
            has_limit = re.search(r"(\s-[0-9]+|\s-n\s*\d+|\s--max-count[=\s]\d+)", cmd)
            if not has_limit:
                # Add -n 10 --oneline
                if "--oneline" not in cmd:
                    new_cmd = re.sub(r"\bgit\s+log\b", "git log -n 10 --oneline", cmd, count=1)
                else:
                    new_cmd = re.sub(r"\bgit\s+log\b", "git log -n 10", cmd, count=1)
                est_saved = 1200
                return new_cmd, est_saved, "Capped unbounded git log to 10 entries"

        # 3. pytest optimization (Compact quiet traceback)
        if re.search(r"\bpytest\b", cmd):
            has_tb = "--tb=" in cmd or "-q" in cmd
            if not has_tb:
                new_cmd = f"{cmd} -q --tb=short"
                est_saved = 800
                return new_cmd, est_saved, "Applied short traceback to pytest"

        # 4. find command unbounded recursion
        if re.search(r"\bfind\s+(\.|\/|\w+)\s*$", cmd) or re.search(r"\bfind\s+\.\s+-name\b", cmd):
            if "node_modules" not in cmd and ".git" not in cmd:
                new_cmd = f"{cmd} -not -path '*/.*' -not -path '*/node_modules/*' -not -path '*/venv/*'"
                est_saved = 1500
                return new_cmd, est_saved, "Pruned .git, node_modules, and venv from find"

        # 5. git grep search (Exclude lockfiles & directories via pathspecs)
        if re.search(r"\bgit\b.*?\bgrep\b", cmd):
            if ":!*.lock" not in cmd and ":!bun.lock" not in cmd:
                git_excludes = "':!*.lock' ':!bun.lock' ':!bun.lockb' ':!package-lock.json' ':!dist' ':!build' ':!node_modules'"
                stages = [s.strip() for s in re.split(r'(?<!\|)\|(?!\|)', cmd)]
                for i, stage in enumerate(stages):
                    if re.search(r"\bgit\b.*?\bgrep\b", stage):
                        if "||" in stage:
                            left, right = stage.split("||", 1)
                            left = left.strip()
                            if " -- " in left:
                                left = f"{left} {git_excludes}"
                            else:
                                left = f"{left} -- . {git_excludes}"
                            stages[i] = f"{left} || {right.strip()}"
                        elif " -- " in stage:
                            stages[i] = f"{stage} {git_excludes}"
                        else:
                            stages[i] = f"{stage} -- . {git_excludes}"
                new_cmd = " | ".join(stages)
                has_limiter = any(re.search(r'\b(head|tail|less)\b', s) for s in stages)
                if not has_limiter:
                    new_cmd = f"{new_cmd} | head -n 30"
                est_saved = 3500
                return new_cmd, est_saved, "Excluded lockfiles from git grep and capped output"

        # 6. grep / ripgrep unbounded search (only when recursive or scanning directory)
        if re.search(r"\b(grep|egrep)\b", cmd) and not re.search(r"\bgit\b.*?\bgrep\b", cmd):
            is_recursive = bool(re.search(r"\b(grep|egrep)\b.*?\s-[a-zA-Z]*[rR]", cmd))
            if is_recursive and "--exclude-dir" not in cmd and "node_modules" not in cmd:
                excludes = "--exclude='*.lock' --exclude='*.lockb' --exclude-dir={dist,build,node_modules,.git,coverage,__pycache__,.next,data}"
                stages = [s.strip() for s in re.split(r'(?<!\|)\|(?!\|)', cmd)]
                for i, stage in enumerate(stages):
                    if re.search(r"\b(grep|egrep)\b", stage):
                        stages[i] = f"{stage} {excludes}"
                new_cmd = " | ".join(stages)
                has_limiter = any(re.search(r'\b(head|tail|less)\b', s) for s in stages)
                if not has_limiter:
                    new_cmd = f"{new_cmd} | head -n 30"
                est_saved = 2500
                return new_cmd, est_saved, "Sanitized grep directories and capped output to 30 matches"

        # 7. ls command optimization (Replace verbose permissions/timestamps with compact single-column format)
        if re.search(r"\bls\b", cmd):
            if re.search(r"\bls\s+-[a-zA-Z]*(l|a)[a-zA-Z]*\b", cmd):
                new_cmd = re.sub(r"\bls\s+-[a-zA-Z]+\b", "ls -1 -p", cmd)
                est_saved = 380
                return new_cmd, est_saved, "Replaced verbose ls flags with compact, secret-safe format (-1 -p)"

        return None, 0, ""
