"""
TokenSafe Scoped Search Engine.
High-efficiency, token-bounded symbol and text search across codebases.
Replaces uncontrolled grep/find commands with bounded, clean, token-efficient results.
"""
import os
import re
import fnmatch
from typing import Dict, List, Optional, Any, Tuple

IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".turbo", ".idea", ".vscode", "coverage", ".pytest_cache",
    ".mypy_cache", ".gemini", ".system_generated", "data", "tokensafe.egg-info"
}

IGNORED_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav",
    ".zip", ".tar", ".gz", ".tgz", ".whl", ".jar", ".db", ".sqlite",
    ".sqlite3", ".wasm"
}

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "composer.lock", "Cargo.lock", "flake.lock"
}

CODE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".pyw", ".go", ".rs", ".java", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".php", ".rb", ".vue", ".svelte", ".swift", ".kt", ".kts", ".cs", ".scala",
    ".sh", ".bash", ".zsh"
}

CONFIG_EXTENSIONS = {
    ".json", ".toml", ".yaml", ".yml", ".ini", ".conf", ".cfg", ".xml"
}

CONFIG_FILENAMES = {
    "package.json", "pyproject.toml", "plugin.json", "tsconfig.json",
    "dockerfile", "dockerfile.cloud", "docker-compose.yml", ".env.example",
    "vite.config.ts", "vite.config.js", "next.config.js", "webpack.config.js"
}


class ScopedSearch:
    """Safe, bounded search engine replacing terminal grep/find."""

    @classmethod
    def is_match_file(cls, filename: str, rel_path: str, scope: str) -> bool:
        """Determines if a file matches the target search scope."""
        basename = os.path.basename(filename)
        lower_base = basename.lower()
        ext = os.path.splitext(filename)[1].lower()

        # Reject binary / lockfiles
        if ext in IGNORED_EXTENSIONS or lower_base in LOCKFILES:
            return False

        if scope == "code":
            return ext in CODE_EXTENSIONS
        elif scope == "config":
            return lower_base in CONFIG_FILENAMES or ext in CONFIG_EXTENSIONS
        elif scope == "all":
            return True
        return True

    @classmethod
    def search(
        cls,
        query: str,
        project_path: Optional[str] = None,
        scope: str = "all",
        max_results: int = 25,
        case_sensitive: bool = False,
        use_regex: bool = False
    ) -> Dict[str, Any]:
        """
        Executes a bounded code/config search and returns structured results and compact markdown.
        """
        if not query or not query.strip():
            return {
                "success": False,
                "error": "Query string cannot be empty",
                "matches": [],
                "total_matches": 0,
                "markdown": "No search query provided."
            }

        root_dir = os.path.abspath(project_path or os.getcwd())
        if not os.path.isdir(root_dir):
            return {
                "success": False,
                "error": f"Invalid project path: {root_dir}",
                "matches": [],
                "total_matches": 0,
                "markdown": f"Invalid project path: {root_dir}"
            }

        query = query.strip()
        scope = (scope or "all").lower().strip()
        max_results = min(100, max(1, int(max_results)))

        # Compile search pattern
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            if use_regex:
                pattern = re.compile(query, flags)
            else:
                pattern = re.compile(re.escape(query), flags)
        except Exception as e:
            return {
                "success": False,
                "error": f"Invalid regex pattern: {str(e)}",
                "matches": [],
                "total_matches": 0,
                "markdown": f"Regex compilation error: {str(e)}"
            }

        matches: List[Dict[str, Any]] = []
        total_scanned_files = 0
        hit_files = set()

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Prune ignored directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORED_DIRS and not d.startswith(".")
            ]

            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")

                if not cls.is_match_file(fname, rel_path, scope):
                    continue

                # Skip files larger than 1MB to prevent memory exhaustion
                try:
                    if os.path.getsize(full_path) > 1024 * 1024:
                        continue
                except OSError:
                    continue

                total_scanned_files += 1

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_idx, line in enumerate(f, 1):
                            if pattern.search(line):
                                clean_line = line.strip()
                                # Truncate long lines to 100 characters for compact token output
                                if len(clean_line) > 100:
                                    clean_line = clean_line[:97] + "..."

                                matches.append({
                                    "file": rel_path,
                                    "line": line_idx,
                                    "text": clean_line
                                })
                                hit_files.add(rel_path)

                                if len(matches) >= max_results:
                                    break
                except Exception:
                    continue

                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        # Generate ultra-compact Markdown output (< 150 tokens)
        lines = [
            f"### TokenSafe Scoped Search: '{query}' ({len(matches)} match{'es' if len(matches) != 1 else ''} in {len(hit_files)} file{'s' if len(hit_files) != 1 else ''}, scope: {scope})"
        ]
        if not matches:
            lines.append(f"_No matches found across {total_scanned_files} scanned files._")
        else:
            for m in matches:
                lines.append(f"- `{m['file']}:{m['line']}`: `{m['text']}`")

        if len(matches) >= max_results:
            lines.append(f"_Results capped at {max_results} matches._")

        return {
            "success": True,
            "query": query,
            "scope": scope,
            "total_matches": len(matches),
            "files_matched": len(hit_files),
            "scanned_files": total_scanned_files,
            "matches": matches,
            "markdown": "\n".join(lines)
        }
