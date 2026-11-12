"""Central data-directory resolution for TokenSafe.

Priority order:
1. ``TOKENSAFE_DATA_DIR`` — explicit override (tests, Docker ``/app/data``).
   Honored verbatim, no migration: the caller controls the environment.
2. ``~/.tokensafe/data`` — the canonical, user-level directory shared by ALL
   agents (Antigravity, Claude Code, Cursor, Codex, ...). Metrics, caches and
   telemetry state live in one SQLite/JSON set regardless of how TokenSafe was
   installed (git clone, npm, pip) or which agent runs the hook.

Migration (only when resolving the canonical dir): well-known state files are
carried over copy-if-absent from the legacy ``<repo>/data`` directory first
(the historical authoritative location), then from ``PLUGIN_DATA`` (the
per-plugin dir Antigravity exposes, which earlier builds migrated into).
``PLUGIN_DATA`` is a migration source only — it never wins resolution, so the
active directory is identical for every agent on the machine.
"""

import os
import shutil
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Well-known state files carried over into the canonical directory.
_MIGRATION_FILES = (
    "metrics.db",
    "version_cache.json",
    "sync_state.json",
    "license.json",
    "config.json",
)


def user_data_dir() -> str:
    """The canonical per-user data directory shared across all agents."""
    return os.path.join(os.path.expanduser("~"), ".tokensafe", "data")


def legacy_data_dir() -> str:
    """The historical data directory inside the plugin repo (migration source)."""
    return os.path.join(PROJECT_ROOT, "data")


def plugin_data_dir() -> str:
    """Antigravity's per-plugin persistent directory (migration source)."""
    return os.environ.get("PLUGIN_DATA") or ""


def resolve_data_dir() -> str:
    """Returns the active data directory according to the priority order above."""
    explicit = os.environ.get("TOKENSAFE_DATA_DIR")
    if explicit:
        return explicit
    canonical = user_data_dir()
    _migrate_into(canonical)
    return canonical


def _migrate_into(target: str) -> None:
    """One-time copy-if-absent carry-over from legacy and plugin directories.

    Uses a sentinel marker (.migrated) once checked, and SQLite's online
    backup API for metrics.db so active WAL transactions are never truncated."""
    sentinel = os.path.join(target, ".migrated")
    if os.path.isfile(sentinel):
        return
    sources = [legacy_data_dir()]
    if plugin_data_dir():
        sources.append(plugin_data_dir())
    sources = [s for s in sources if os.path.isdir(s)]
    if not sources:
        try:
            os.makedirs(target, exist_ok=True)
            with open(sentinel, "w", encoding="utf-8") as f:
                f.write("1")
        except Exception:
            pass
        return
    try:
        os.makedirs(target, exist_ok=True)
        for source in sources:
            if os.path.abspath(source) == os.path.abspath(target):
                continue
            for name in _MIGRATION_FILES:
                src_file = os.path.join(source, name)
                dst_file = os.path.join(target, name)
                if os.path.isfile(src_file) and not os.path.exists(dst_file):
                    tmp_file = f"{dst_file}.migrating.{os.getpid()}"
                    if name == "metrics.db":
                        try:
                            src_conn = sqlite3.connect(src_file, timeout=5.0)
                            dst_conn = sqlite3.connect(tmp_file, timeout=5.0)
                            try:
                                src_conn.backup(dst_conn)
                            finally:
                                dst_conn.close()
                                src_conn.close()
                            os.replace(tmp_file, dst_file)
                            continue
                        except Exception:
                            if os.path.exists(tmp_file):
                                try:
                                    os.unlink(tmp_file)
                                except OSError:
                                    pass
                    shutil.copy2(src_file, tmp_file)
                    os.replace(tmp_file, dst_file)
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        pass
