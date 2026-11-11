"""Central data-directory resolution for TokenSafe.

Priority order:
1. ``TOKENSAFE_DATA_DIR`` — explicit override (tests, Docker, CLI). Honored
   verbatim, no migration: the caller controls the environment.
2. ``PLUGIN_DATA`` — the persistent per-plugin directory Antigravity exposes
   to installed-plugin hooks and MCP servers (survives plugin updates and
   reinstalls, unlike the plugin folder itself).
3. Legacy ``<repo>/data`` — the historical default, kept as the fallback and
   as the migration source.

When ``PLUGIN_DATA`` is active, well-known state files are migrated once from
the legacy directory copy-if-absent, so upgrading from v2.2.x keeps metrics,
caches and license state.
"""

import os
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Well-known state files carried over from the legacy <repo>/data directory.
_MIGRATION_FILES = (
    "metrics.db",
    "version_cache.json",
    "sync_state.json",
    "license.json",
    "config.json",
)


def legacy_data_dir() -> str:
    """The historical data directory inside the plugin repo (migration source)."""
    return os.path.join(PROJECT_ROOT, "data")


def resolve_data_dir() -> str:
    """Returns the active data directory according to the priority order above."""
    explicit = os.environ.get("TOKENSAFE_DATA_DIR")
    if explicit:
        return explicit
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        _migrate_legacy_data(plugin_data)
        return plugin_data
    return legacy_data_dir()


def _migrate_legacy_data(target: str) -> None:
    """One-time copy-if-absent carry-over from the legacy directory.

    Copy-if-absent (never overwrite) keeps the target authoritative after a
    plugin update: the legacy dir then holds stale files, and re-copying them
    would roll state back. The tmp+os.replace dance keeps concurrent hook
    processes from observing a half-written file."""
    source = legacy_data_dir()
    if not os.path.isdir(source) or os.path.abspath(source) == os.path.abspath(target):
        return
    try:
        os.makedirs(target, exist_ok=True)
        for name in _MIGRATION_FILES:
            src_file = os.path.join(source, name)
            dst_file = os.path.join(target, name)
            if os.path.isfile(src_file) and not os.path.exists(dst_file):
                tmp_file = dst_file + ".migrating"
                shutil.copy2(src_file, tmp_file)
                os.replace(tmp_file, dst_file)
    except Exception:
        pass
