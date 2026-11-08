"""
Metrics Storage & Analytics Engine for Antigravity Token Optimizer.
Tracks every token saved across sessions, calculating percentage gains and USD value.
Uses SQLite for persistent, fast, zero-dependency storage.
"""

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Generator

USD_PER_TOKEN = 1.50 / 1_000_000.0


class MetricsTracker:
    """Manages persistent token savings metrics in SQLite with lifetime rollups and retention."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = os.environ.get("TOKENSAFE_DATA_DIR")
            if not data_dir:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.environ.get("TOKENSAFE_DB_PATH", os.path.join(data_dir, "metrics.db"))

        self.db_path = db_path
        self.retention_days = int(os.environ.get("TOKENSAFE_RETENTION_DAYS", "30"))
        self.max_events = int(os.environ.get("TOKENSAFE_MAX_EVENTS", "5000"))
        self._event_counter = 0
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lifetime_aggregates (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_events INTEGER DEFAULT 0,
                    total_original_tokens INTEGER DEFAULT 0,
                    total_optimized_tokens INTEGER DEFAULT 0,
                    total_saved_tokens INTEGER DEFAULT 0,
                    total_usd_saved REAL DEFAULT 0.0,
                    breakdown TEXT DEFAULT '{}'
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO lifetime_aggregates (
                    id, total_events, total_original_tokens, total_optimized_tokens,
                    total_saved_tokens, total_usd_saved, breakdown
                ) VALUES (1, 0, 0, 0, 0, 0.0, '{}')
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id)")

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

        # Periodic amortized auto-retention check (every 100 events)
        self._event_counter += 1
        if self._event_counter >= 100:
            self._event_counter = 0
            try:
                self.prune(days=self.retention_days, max_events=self.max_events, vacuum=False)
            except Exception:
                pass

        return {
            "saved_tokens": saved_tokens,
            "reduction_pct": round(reduction_pct, 1),
            "est_usd_saved": est_usd
        }

    def get_summary(self) -> Dict[str, Any]:
        """Returns cumulative statistics across all sessions, including lifetime rollups."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Lifetime aggregates
            cursor.execute("SELECT * FROM lifetime_aggregates WHERE id = 1")
            life_row = cursor.fetchone()
            life_events = life_row["total_events"] if life_row else 0
            life_orig = life_row["total_original_tokens"] if life_row else 0
            life_opt = life_row["total_optimized_tokens"] if life_row else 0
            life_saved = life_row["total_saved_tokens"] if life_row else 0
            life_usd = life_row["total_usd_saved"] if life_row else 0.0
            life_breakdown = json.loads(life_row["breakdown"] or "{}") if life_row else {}

            # 2. Active events
            cursor.execute("""
                SELECT 
                    COUNT(*) as active_events,
                    COALESCE(SUM(original_tokens), 0) as active_orig,
                    COALESCE(SUM(optimized_tokens), 0) as active_opt,
                    COALESCE(SUM(saved_tokens), 0) as active_saved,
                    COALESCE(SUM(est_usd_saved), 0.0) as active_usd
                FROM events
            """)
            ev_row = cursor.fetchone()

            cursor.execute("SELECT COUNT(DISTINCT session_id) as session_count FROM sessions")
            sess_row = cursor.fetchone()
            session_count = sess_row["session_count"] if sess_row else 0

            # 3. Breakdown of active events
            cursor.execute("""
                SELECT event_type, COUNT(*) as count, COALESCE(SUM(saved_tokens), 0) as saved
                FROM events
                GROUP BY event_type
            """)
            combined_breakdown = dict(life_breakdown)
            for r in cursor.fetchall():
                ev_type = r["event_type"]
                if ev_type not in combined_breakdown:
                    combined_breakdown[ev_type] = {"count": 0, "saved": 0}
                else:
                    combined_breakdown[ev_type] = dict(combined_breakdown[ev_type])
                combined_breakdown[ev_type]["count"] += r["count"]
                combined_breakdown[ev_type]["saved"] += r["saved"]

        total_events = life_events + (ev_row["active_events"] if ev_row else 0)
        total_orig = life_orig + (ev_row["active_orig"] if ev_row else 0)
        total_opt = life_opt + (ev_row["active_opt"] if ev_row else 0)
        total_saved = life_saved + (ev_row["active_saved"] if ev_row else 0)
        total_usd = life_usd + (ev_row["active_usd"] if ev_row else 0.0)
        overall_reduction = (total_saved / total_orig * 100.0) if total_orig > 0 else 0.0

        return {
            "session_count": max(session_count, 1 if total_events > 0 else 0),
            "total_events": total_events,
            "total_original_tokens": total_orig,
            "total_optimized_tokens": total_opt,
            "total_saved_tokens": total_saved,
            "overall_reduction_pct": round(overall_reduction, 1),
            "total_usd_saved": round(total_usd, 4),
            "breakdown": combined_breakdown
        }

    def get_monthly_saved_tokens(self) -> int:
        """Returns total tokens saved in the trailing 30 days for quota calculations."""
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(SUM(saved_tokens), 0) as monthly_saved
                FROM events
                WHERE timestamp >= ?
            """, (thirty_days_ago,))
            row = cursor.fetchone()
            return int(row["monthly_saved"]) if row else 0

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

    def prune(
        self,
        days: Optional[int] = None,
        max_events: Optional[int] = None,
        vacuum: bool = True
    ) -> Dict[str, Any]:
        """
        Prunes raw events older than `days` or exceeding `max_events`.
        Preserves cumulative metrics by rolling pruned data into `lifetime_aggregates`.
        """
        db_size_before = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

        where_clauses = []
        params = []

        if days is not None and days > 0:
            cutoff_time = (datetime.now() - timedelta(days=days)).isoformat()
            where_clauses.append("timestamp < ?")
            params.append(cutoff_time)

        if max_events is not None and max_events > 0:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?",
                    (max_events,)
                )
                row = cursor.fetchone()
                if row:
                    cutoff_id = row[0]
                    where_clauses.append("id <= ?")
                    params.append(cutoff_id)

        if not where_clauses:
            return {
                "pruned_events": 0,
                "db_size_before": db_size_before,
                "db_size_after": db_size_before
            }

        combined_where = " OR ".join(where_clauses)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Calculate aggregates for events to be pruned
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as cnt,
                    COALESCE(SUM(original_tokens), 0) as orig,
                    COALESCE(SUM(optimized_tokens), 0) as opt,
                    COALESCE(SUM(saved_tokens), 0) as saved,
                    COALESCE(SUM(est_usd_saved), 0.0) as usd
                FROM events
                WHERE {combined_where}
            """, params)
            agg_row = cursor.fetchone()
            prune_count = agg_row["cnt"] if agg_row else 0

            if prune_count > 0:
                # 2. Breakdown of pruned events
                cursor.execute(f"""
                    SELECT event_type, COUNT(*) as cnt, COALESCE(SUM(saved_tokens), 0) as saved
                    FROM events
                    WHERE {combined_where}
                    GROUP BY event_type
                """, params)
                pruned_breakdown = {
                    r["event_type"]: {"count": r["cnt"], "saved": r["saved"]}
                    for r in cursor.fetchall()
                }

                # 3. Fetch existing lifetime aggregates
                cursor.execute("SELECT * FROM lifetime_aggregates WHERE id = 1")
                life_row = cursor.fetchone()
                current_breakdown = json.loads(life_row["breakdown"] or "{}") if life_row else {}

                for ev_type, vals in pruned_breakdown.items():
                    if ev_type not in current_breakdown:
                        current_breakdown[ev_type] = {"count": 0, "saved": 0}
                    else:
                        current_breakdown[ev_type] = dict(current_breakdown[ev_type])
                    current_breakdown[ev_type]["count"] += vals["count"]
                    current_breakdown[ev_type]["saved"] += vals["saved"]

                # 4. Update lifetime_aggregates
                cursor.execute("""
                    UPDATE lifetime_aggregates SET
                        total_events = total_events + ?,
                        total_original_tokens = total_original_tokens + ?,
                        total_optimized_tokens = total_optimized_tokens + ?,
                        total_saved_tokens = total_saved_tokens + ?,
                        total_usd_saved = total_usd_saved + ?,
                        breakdown = ?
                    WHERE id = 1
                """, (
                    agg_row["cnt"], agg_row["orig"], agg_row["opt"],
                    agg_row["saved"], agg_row["usd"],
                    json.dumps(current_breakdown, ensure_ascii=False)
                ))

                # 5. Delete pruned events
                cursor.execute(f"DELETE FROM events WHERE {combined_where}", params)

        if vacuum and prune_count > 0:
            self.vacuum()

        db_size_after = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

        return {
            "pruned_events": prune_count,
            "db_size_before": db_size_before,
            "db_size_after": db_size_after
        }

    def vacuum(self):
        """Reclaims unused disk space by running VACUUM on SQLite."""
        try:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.execute("VACUUM")
            conn.close()
        except Exception:
            pass

    def reset(self):
        """Resets all metrics data."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events")
            cursor.execute("DELETE FROM sessions")
            cursor.execute("""
                UPDATE lifetime_aggregates SET
                    total_events = 0,
                    total_original_tokens = 0,
                    total_optimized_tokens = 0,
                    total_saved_tokens = 0,
                    total_usd_saved = 0.0,
                    breakdown = '{}'
                WHERE id = 1
            """)
        self.vacuum()
