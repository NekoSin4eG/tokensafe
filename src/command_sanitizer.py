"""
Command Sanitizer: Intercepts and rewrites shell commands in PreToolUse
to prevent context window poisoning from lockfile diffs, unconstrained logs,
and verbose test output.
"""

import re
from typing import Tuple, Dict, Any, Optional

class CommandSanitizer:
    """Optimizes terminal command lines to eliminate token waste."""

    # Lockfiles and binary patterns that should never be dumped in git diffs
    LOCKFILE_EXCLUDES = [
        "':!package-lock.json'",
        "':!pnpm-lock.yaml'",
        "':!yarn.lock'",
        "':!Cargo.lock'",
        "':!go.sum'",
        "':!composer.lock'",
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

        # 1. git diff optimization (Exclude massive lockfiles and bundles)
        if re.search(r"\bgit\s+diff\b", cmd):
            # Check if user already specified lockfile exclusion
            if ":!package-lock" not in cmd and ":!*.lock" not in cmd and ":!" not in cmd:
                # Append pathspec exclusions
                # If " -- " already exists, append exclusions; otherwise add " -- "
                excludes_str = " ".join(cls.LOCKFILE_EXCLUDES)
                if " -- " in cmd:
                    new_cmd = f"{cmd} {excludes_str}"
                else:
                    new_cmd = f"{cmd} -- . {excludes_str}"

                # Typical lockfile diff can easily exceed 2,000 - 10,000 tokens
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

        return None, 0, ""
