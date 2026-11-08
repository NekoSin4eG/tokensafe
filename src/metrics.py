"""
Metrics Storage & Analytics Engine for Antigravity Token Optimizer.
Tracks every token saved across sessions, calculating percentage gains and USD value.
Uses SQLite for persistent, fast, zero-dependency storage.
"""

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any, Generator

USD_PER_TOKEN = 1.50 / 1_000_000.0


class MetricsTracker:
    """Manages persistent token savings metrics in SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "metrics.db")

        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    last_updated TEXT NOT NULL,
                    total_original_tokens INTEGER DEFAULT 0,
                    total_optimized_tokens INTEGER DEFAULT 0,
                    total_saved_tokens INTEGER DEFAULT 0,
                    total_usd_saved REAL DEFAULT 0.0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    optimized_tokens INTEGER NOT NULL,
                    saved_tokens INTEGER NOT NULL,
                    reduction_pct REAL NOT NULL,
                    est_usd_saved REAL NOT NULL,
                    details TEXT
                )
            """)

    def record_event(
        self,
        session_id: str,
        event_type: str,
        original_tokens: int,
        optimized_tokens: int,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Records an optimization event and updates session totals."""
        saved_tokens = max(0, original_tokens - optimized_tokens)
        reduction_pct = (saved_tokens / original_tokens * 100.0) if original_tokens > 0 else 0.0
        est_usd = round(saved_tokens * USD_PER_TOKEN, 6)
        now_iso = datetime.now().isoformat()
        details_json = json.dumps(details or {}, ensure_ascii=False)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Insert event
            cursor.execute("""
                INSERT INTO events (
                    session_id, timestamp, event_type, original_tokens,
                    optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, now_iso, event_type, original_tokens,
                  optimized_tokens, saved_tokens, round(reduction_pct, 2), est_usd, details_json))

            # 2. Upsert session
            cursor.execute("""
                INSERT INTO sessions (
                    session_id, created_at, last_updated, total_original_tokens,
                    total_optimized_tokens, total_saved_tokens, total_usd_saved
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_updated = excluded.last_updated,
                    total_original_tokens = total_original_tokens + excluded.total_original_tokens,
                    total_optimized_tokens = total_optimized_tokens + excluded.total_optimized_tokens,
                    total_saved_tokens = total_saved_tokens + excluded.total_saved_tokens,
                    total_usd_saved = total_usd_saved + excluded.total_usd_saved
            """, (session_id, now_iso, now_iso, original_tokens,
                  optimized_tokens, saved_tokens, est_usd))

        return {
            "saved_tokens": saved_tokens,
            "reduction_pct": round(reduction_pct, 1),
            "est_usd_saved": est_usd
        }

    def get_summary(self) -> Dict[str, Any]:
        """Returns cumulative statistics across all sessions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(DISTINCT session_id) as session_count,
                    COUNT(*) as total_events,
                    COALESCE(SUM(original_tokens), 0) as total_orig,
                    COALESCE(SUM(optimized_tokens), 0) as total_opt,
                    COALESCE(SUM(saved_tokens), 0) as total_saved,
                    COALESCE(SUM(est_usd_saved), 0.0) as total_usd
                FROM events
            """)
            row = cursor.fetchone()

            cursor.execute("""
                SELECT event_type, COUNT(*) as count, COALESCE(SUM(saved_tokens), 0) as saved
                FROM events
                GROUP BY event_type
            """)
            breakdown = {r["event_type"]: {"count": r["count"], "saved": r["saved"]} for r in cursor.fetchall()}

        total_orig = row["total_orig"]
        total_saved = row["total_saved"]
        overall_reduction = (total_saved / total_orig * 100.0) if total_orig > 0 else 0.0

        return {
            "session_count": row["session_count"],
            "total_events": row["total_events"],
            "total_original_tokens": total_orig,
            "total_optimized_tokens": row["total_opt"],
            "total_saved_tokens": total_saved,
            "overall_reduction_pct": round(overall_reduction, 1),
            "total_usd_saved": round(row["total_usd"], 4),
            "breakdown": breakdown
        }

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns the most recent optimization events."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, session_id, timestamp, event_type, original_tokens,
                       optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details
                FROM events
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

        events = []
        for r in rows:
            ev = dict(r)
            try:
                ev["details"] = json.loads(ev["details"])
            except Exception:
                pass
            events.append(ev)
        return events

    def get_last_event(self, session_id: str, event_type: str) -> Optional[Dict[str, Any]]:
        """Returns the most recent event of a given type for a session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, session_id, timestamp, event_type, original_tokens,
                       optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details
                FROM events
                WHERE session_id = ? AND event_type = ?
                ORDER BY id DESC
                LIMIT 1
            """, (session_id, event_type))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["details"] = json.loads(res["details"])
            except Exception:
                pass
            return res

    def recalculate_sessions(self):
        """Recalculates sessions table aggregates directly from events table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions")
            cursor.execute("""
                INSERT INTO sessions (
                    session_id, created_at, last_updated,
                    total_original_tokens, total_optimized_tokens,
                    total_saved_tokens, total_usd_saved
                )
                SELECT 
                    session_id,
                    MIN(timestamp) as created_at,
                    MAX(timestamp) as last_updated,
                    COALESCE(SUM(original_tokens), 0) as total_original_tokens,
                    COALESCE(SUM(optimized_tokens), 0) as total_optimized_tokens,
                    COALESCE(SUM(saved_tokens), 0) as total_saved_tokens,
                    COALESCE(SUM(est_usd_saved), 0.0) as total_usd_saved
                FROM events
                GROUP BY session_id
            """)

    def reset(self):
        """Resets all metrics data."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events")
            cursor.execute("DELETE FROM sessions")
