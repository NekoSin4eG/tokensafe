"""
TokenSafe Subagent Firewall: Cross-Agent Deduplication Cache.
Detects redundant file inspection across parent-subagent workflows and swarms.
If an agent or subagent requests a file already ingested within the session,
serves cached AST representation and suppresses quadratic token waste.

Persistent layer: entries are additionally keyed by the FILE'S CONTENT HASH
(sha256), so a cache hit survives (a) session restarts and (b) `git checkout`
mtime resets — content is the only truth. A hash mismatch (real edit) invalidates.
"""

import os
import time
import sqlite3
import hashlib
from typing import Tuple, Optional, Dict, Any
from src.metrics import MetricsTracker
from src.tokenizer import count_tokens

class AgentFirewall:
    """Manages cross-agent shared file cache within and across sessions."""

    _INITIALIZED_DBS = set()
    _MAX_CONTENT_CHARS = 262_144   # refuse to cache >256k-char artifacts
    _PRUNE_INTERVAL = 3600.0       # amortized hourly prune of the persistent layer
    _PRUNE_KEEP = 500
    _last_prune = 0.0

    @classmethod
    def _ensure_table(cls, tracker: MetricsTracker):
        """Initializes the subagent cache table once per database path."""
        if tracker.db_path in cls._INITIALIZED_DBS:
            return
        with tracker._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subagent_file_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    tokens INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, file_path, symbol)
                );
            """)
            # Migration for pre-hash databases (public export & upgrades).
            try:
                conn.execute("ALTER TABLE subagent_file_cache ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';")
            except sqlite3.OperationalError:
                pass  # column already present
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subagent_cache ON subagent_file_cache(session_id, file_path);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subagent_hash ON subagent_file_cache(file_path, symbol, content_hash);")
        cls._INITIALIZED_DBS.add(tracker.db_path)

    @staticmethod
    def _file_content_hash(file_path: str) -> str:
        """sha256 of the file's RAW bytes — the diff-aware truth that survives
        git checkout mtime resets."""
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return ""

    @classmethod
    def _maybe_prune(cls, tracker: MetricsTracker):
        """Amortized retention: keeps the persistent layer bounded without a cron."""
        now = time.time()
        if now - cls._last_prune < cls._PRUNE_INTERVAL:
            return
        cls._last_prune = now
        try:
            with tracker._get_connection() as conn:
                conn.execute("""
                    DELETE FROM subagent_file_cache
                    WHERE id NOT IN (
                        SELECT id FROM subagent_file_cache ORDER BY created_at DESC LIMIT ?
                    );
                """, (cls._PRUNE_KEEP,))
        except Exception:
            pass

    @classmethod
    def check_cache(
        cls,
        session_id: str,
        file_path: str,
        symbol: str = "",
        tracker: Optional[MetricsTracker] = None,
        allow_pointer: bool = False,
    ) -> Tuple[bool, Optional[str], int]:
        """
        Two-stage lookup:
        1. In-session entry with unchanged mtime (fast path, unchanged semantics).
        2. Cross-session entry whose content_hash matches the file's CURRENT
           bytes — survives restarts and git-checkout mtime resets; any real
           edit changes the hash and invalidates the entry.
        When allow_pointer=True and hit_scope == 'session' (caller already
        holds this exact content in its active context), returns a compact
        self-healing pointer instead of re-emitting the skeleton body.
        Returns: (is_hit, cached_content_or_none, tokens_saved)
        """
        if not file_path or not os.path.exists(file_path):
            return False, None, 0

        tracker = tracker or MetricsTracker()
        cls._ensure_table(tracker)

        try:
            current_mtime = os.path.getmtime(file_path)
        except OSError:
            return False, None, 0

        abs_path = os.path.abspath(file_path)
        symbol_key = (symbol or "").strip()
        current_hash = cls._file_content_hash(abs_path)

        hit_data = None
        hit_scope = "session"

        def _query():
            nonlocal hit_data, hit_scope
            with tracker._get_connection() as conn:
                # Stage 1: in-session, mtime-validated
                row = conn.execute("""
                    SELECT id, content, tokens, mtime, hit_count
                    FROM subagent_file_cache
                    WHERE session_id = ? AND file_path = ? AND symbol = ?;
                """, (session_id, abs_path, symbol_key)).fetchone()

                if row and row["mtime"] >= current_mtime:
                    hit_data = (row["id"], row["content"], row["tokens"], row["hit_count"] + 1)
                elif current_hash:
                    # Stage 2: cross-session, content-hash-validated
                    row2 = conn.execute("""
                        SELECT id, content, tokens, hit_count
                        FROM subagent_file_cache
                        WHERE file_path = ? AND symbol = ? AND content_hash = ?
                        ORDER BY created_at DESC LIMIT 1;
                    """, (abs_path, symbol_key, current_hash)).fetchone()
                    if row2:
                        hit_data = (row2["id"], row2["content"], row2["tokens"], row2["hit_count"] + 1)
                        hit_scope = "persistent"

        try:
            _query()
        except sqlite3.OperationalError:
            cls._INITIALIZED_DBS.discard(tracker.db_path)
            cls._ensure_table(tracker)
            _query()

        if hit_data:
            cache_id, content, tokens, hit_count = hit_data
            try:
                with tracker._get_connection() as conn:
                    conn.execute("UPDATE subagent_file_cache SET hit_count = ? WHERE id = ?;", (hit_count, cache_id))
            except Exception:
                pass

            short_hash = current_hash[:8] if current_hash else "cached"
            use_pointer = bool(allow_pointer and hit_scope == "session" and tokens > 60)

            if use_pointer:
                sym_label = f"::{symbol_key}" if symbol_key else ""
                payload = (
                    f"// [TokenSafe Subagent Firewall: Pointer Hit for '{os.path.basename(abs_path)}{sym_label}' "
                    f"(unchanged, sha256:{short_hash}) — reuse the content returned earlier in this session; "
                    f"if you are a fresh subagent or your context was compacted, re-call smart_read_file with force_full=true]\n"
                )
                opt_tokens = count_tokens(payload)
            else:
                scope_label = "Persistent cache" if hit_scope == "persistent" else "Reused session read"
                sym_label = f"::{symbol_key}" if symbol_key else ""
                # Static deterministic header (no changing hit counters that bust LLM prefix KV-cache)
                header = (
                    f"// [TokenSafe Subagent Firewall: {scope_label} for "
                    f"'{os.path.basename(abs_path)}{sym_label}' (sha256:{short_hash})]\n"
                )
                payload = header + content
                opt_tokens = 40

            saved = max(0, tokens - opt_tokens)
            try:
                tracker.record_event(
                    session_id=session_id,
                    event_type="SUBAGENT_DEDUP",
                    original_tokens=tokens,
                    optimized_tokens=opt_tokens,
                    details={
                        "target_file": os.path.basename(abs_path),
                        "symbol": symbol_key,
                        "hit_number": hit_count,
                        "scope": hit_scope,
                        "pointer": use_pointer,
                    }
                )
            except Exception:
                pass

            return True, payload, saved

        return False, None, 0

    @classmethod
    def record_cache(
        cls,
        session_id: str,
        file_path: str,
        content: str,
        symbol: str = "",
        tracker: Optional[MetricsTracker] = None
    ):
        """Caches file read content for subagent reuse in the active session
        AND persistently, keyed by content hash."""
        if not file_path or not content or not os.path.exists(file_path):
            return
        if len(content) > cls._MAX_CONTENT_CHARS:
            return  # refuse to persist oversized artifacts

        tracker = tracker or MetricsTracker()
        cls._ensure_table(tracker)

        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            mtime = time.time()

        abs_path = os.path.abspath(file_path)
        symbol_key = (symbol or "").strip()
        tokens = count_tokens(content)
        now = int(time.time())
        content_hash = cls._file_content_hash(abs_path)

        def _insert():
            with tracker._get_connection() as conn:
                conn.execute("""
                    INSERT INTO subagent_file_cache (session_id, file_path, mtime, symbol, content, tokens, created_at, hit_count, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(session_id, file_path, symbol) DO UPDATE SET
                        mtime = excluded.mtime,
                        content = excluded.content,
                        tokens = excluded.tokens,
                        created_at = excluded.created_at,
                        content_hash = excluded.content_hash;
                """, (session_id, abs_path, mtime, symbol_key, content, tokens, now, content_hash))

        try:
            _insert()
        except sqlite3.OperationalError:
            cls._INITIALIZED_DBS.discard(tracker.db_path)
            cls._ensure_table(tracker)
            _insert()
        cls._maybe_prune(tracker)
