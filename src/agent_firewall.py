"""
TokenSafe Subagent Firewall: Cross-Agent Deduplication Cache.
Detects redundant file inspection across parent-subagent workflows and swarms.
If an agent or subagent requests a file already ingested within the session,
serves cached AST representation and suppresses quadratic token waste.
"""

import os
import time
import sqlite3
from typing import Tuple, Optional, Dict, Any
from src.metrics import MetricsTracker
from src.tokenizer import count_tokens

class AgentFirewall:
    """Manages cross-agent shared file cache within active sessions."""

    _INITIALIZED_DBS = set()

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
                    UNIQUE(session_id, file_path, symbol)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subagent_cache ON subagent_file_cache(session_id, file_path);")
        cls._INITIALIZED_DBS.add(tracker.db_path)

    @classmethod
    def check_cache(
        cls,
        session_id: str,
        file_path: str,
        symbol: str = "",
        tracker: Optional[MetricsTracker] = None
    ) -> Tuple[bool, Optional[str], int]:
        """
        Checks if file has already been ingested in this session with unchanged mtime.
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

        with tracker._get_connection() as conn:
            row = conn.execute("""
                SELECT id, content, tokens, mtime, hit_count 
                FROM subagent_file_cache
                WHERE session_id = ? AND file_path = ? AND symbol = ?;
            """, (session_id, abs_path, symbol_key)).fetchone()

            if row and row["mtime"] >= current_mtime:
                # Cache HIT!
                cache_id = row["id"]
                content = row["content"]
                tokens = row["tokens"]
                hit_count = row["hit_count"] + 1

                conn.execute("UPDATE subagent_file_cache SET hit_count = ? WHERE id = ?;", (hit_count, cache_id))

                # Record savings
                tracker.record_event(
                    session_id=session_id,
                    event_type="SUBAGENT_DEDUP",
                    original_tokens=tokens,
                    optimized_tokens=40, # minimal cache reference tokens
                    details={
                        "target_file": os.path.basename(abs_path),
                        "symbol": symbol_key,
                        "hit_number": hit_count
                    }
                )

                header = (
                    f"// [TokenSafe Subagent Firewall: Reused session read for '{os.path.basename(abs_path)}' "
                    f"(Dedup Hit #{hit_count}, Saved ~{tokens - 40} tokens)]\n"
                )
                return True, header + content, max(0, tokens - 40)

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
        """Caches file read content for subagent reuse in the active session."""
        if not file_path or not content or not os.path.exists(file_path):
            return

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

        with tracker._get_connection() as conn:
            conn.execute("""
                INSERT INTO subagent_file_cache (session_id, file_path, mtime, symbol, content, tokens, created_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(session_id, file_path, symbol) DO UPDATE SET
                    mtime = excluded.mtime,
                    content = excluded.content,
                    tokens = excluded.tokens,
                    created_at = excluded.created_at;
            """, (session_id, abs_path, mtime, symbol_key, content, tokens, now))
