"""
Metrics Storage & Analytics Engine for Antigravity Token Optimizer.
Tracks every token saved across sessions, calculating percentage gains and USD value.
Uses SQLite for persistent, fast, zero-dependency storage.
"""

import os
import json
import time
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Generator

from src.paths import resolve_data_dir

USD_PER_TOKEN = 1.50 / 1_000_000.0

# Event types whose `original_tokens` baseline is a counterfactual estimate rather
# than literally metered I/O (see METRICS.md). Everything else counts as measured.
#   GRAPH_TOPOLOGY: "original" is what a hypothetical full-file crawl would have cost.
#   HISTORY_FOLD:   a folded digest is INJECTED as a system message; the full history
#                   physically stays in the context window.
#   PROMPT:         the TOCP form is injected alongside the original prompt until
#                   replace-mode is verified end-to-end.
#   ROLLUP_LIFETIME: pruned-and-rolled-up historical mass of mixed provenance;
#                   conservatively labelled estimated.
# Counterfactual event types: their original_tokens are heuristic guesses of
# output that WOULD have entered context (command rewrites, blocked calls,
# prompt compaction), not metered I/O deltas. See METRICS.md.
ESTIMATED_EVENT_TYPES = frozenset({
    "GRAPH_TOPOLOGY",
    "HISTORY_FOLD",
    "PROMPT",
    "ROLLUP_LIFETIME",
    "COMMAND_FILTER",
    "LOOP_GUARD",
    "SCOPED_SEARCH",
    "DIR_LIST",
    "GUARD_SECRET",
    "GUARD_DESTRUCTIVE",
    "GUARD_TLS_VERIFY_BYPASS",
    "GUARD_JUNK_READ",
    "GUARD_INFINITE",
    "GUARD_RAW_CODE_SEARCH",
})

def resolve_measurement(event_type: str, explicit: Optional[str] = None) -> str:
    """Resolves the measurement label for an event: explicit override wins,
    then the estimated-type map, then 'measured'."""
    if explicit in ("measured", "estimated"):
        return explicit
    return "estimated" if event_type in ESTIMATED_EVENT_TYPES else "measured"

_METRICS_INIT_LOCK = threading.Lock()
_INITIALIZED_METRICS_DBS: set = set()
# Retention / telemetry cadence is wall-clock based (marker file) rather than
# instance-based counters: lifecycle hooks construct a fresh MetricsTracker per
# invocation, so per-instance counters never reached the threshold in production.
_AUTOPRUNE_INTERVAL = float(os.environ.get("TOKENSAFE_AUTOPRUNE_INTERVAL", "3600"))


class MetricsTracker:
    """Manages persistent token savings metrics in SQLite with lifetime rollups and retention."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = resolve_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.environ.get("TOKENSAFE_DB_PATH", os.path.join(data_dir, "metrics.db"))

        self.db_path = os.path.abspath(db_path)
        self.retention_days = int(os.environ.get("TOKENSAFE_RETENTION_DAYS", "30"))
        self.max_events = int(os.environ.get("TOKENSAFE_MAX_EVENTS", "5000"))
        self._event_counter = 0
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=3000;")
        except sqlite3.Error:
            pass
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _maybe_autoprune(cls, db_path: str):
        """
        Wall-clock-gated retention + telemetry trigger, safe across short-lived
        hook processes. A marker file records the last run; an O_EXCL claim file
        prevents stampedes when several hooks fire concurrently.
        """
        marker = os.path.join(os.path.dirname(db_path) or ".", ".metrics_last_prune")
        claim = marker + ".claim"
        _CLAIM_STALE_TTL = 300.0
        try:
            now = time.time()
            if os.path.exists(marker) and (now - os.path.getmtime(marker)) < _AUTOPRUNE_INTERVAL:
                return
            if os.path.exists(claim):
                try:
                    if (now - os.path.getmtime(claim)) > _CLAIM_STALE_TTL:
                        os.unlink(claim)
                except OSError:
                    pass
            try:
                fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                return  # another active process is pruning right now
            try:
                tracker = cls(db_path)
                tracker.prune(days=tracker.retention_days, max_events=tracker.max_events, vacuum=False)
                try:
                    from src.telemetry_sync import TelemetrySync
                    TelemetrySync.sync_background()
                except Exception:
                    pass
            finally:
                try:
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(str(int(time.time())))
                    if os.path.exists(claim):
                        os.unlink(claim)
                except Exception:
                    pass
        except Exception:
            pass

    def _init_db(self):
        with _METRICS_INIT_LOCK:
            if self.db_path in _INITIALIZED_METRICS_DBS and os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                return
            with self._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    uv_row = cursor.execute("PRAGMA user_version;").fetchone()
                    user_ver = int(uv_row[0]) if uv_row else 0
                except Exception:
                    user_ver = 0
                if user_ver >= 2:
                    tbl = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events';").fetchone()
                    if tbl is not None:
                        cursor.execute(
                            "UPDATE events SET measurement = 'estimated' "
                            "WHERE measurement = 'measured' AND event_type IN "
                            "('GRAPH_TOPOLOGY', 'HISTORY_FOLD', 'PROMPT', 'ROLLUP_LIFETIME', "
                            "'COMMAND_FILTER', 'LOOP_GUARD', 'SCOPED_SEARCH', 'DIR_LIST', "
                            "'GUARD_SECRET', 'GUARD_DESTRUCTIVE', 'GUARD_TLS_VERIFY_BYPASS', "
                            "'GUARD_JUNK_READ', 'GUARD_INFINITE', 'GUARD_RAW_CODE_SEARCH');"
                        )
                        _INITIALIZED_METRICS_DBS.add(self.db_path)
                        return
                try:
                    cursor.execute("PRAGMA journal_mode=WAL;")
                except sqlite3.Error:
                    pass
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
                        details TEXT,
                        measurement TEXT NOT NULL DEFAULT 'measured'
                    )
                """)
                # Migration for databases created before the measurement column existed.
                try:
                    cursor.execute(
                        "ALTER TABLE events ADD COLUMN measurement TEXT NOT NULL DEFAULT 'measured';"
                    )
                except sqlite3.OperationalError:
                    pass  # column already present
                # Retroactively label counterfactual event types recorded before the
                # measured/estimated split was introduced (idempotent: new events of
                # these types are already inserted with measurement='estimated').
                cursor.execute(
                    "UPDATE events SET measurement = 'estimated' "
                    "WHERE measurement = 'measured' AND event_type IN "
                    "('GRAPH_TOPOLOGY', 'HISTORY_FOLD', 'PROMPT', 'ROLLUP_LIFETIME', "
                    "'COMMAND_FILTER', 'LOOP_GUARD', 'SCOPED_SEARCH', 'DIR_LIST', "
                    "'GUARD_SECRET', 'GUARD_DESTRUCTIVE', 'GUARD_TLS_VERIFY_BYPASS', "
                    "'GUARD_JUNK_READ', 'GUARD_INFINITE', 'GUARD_RAW_CODE_SEARCH');"
                )
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
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_measurement_type ON events(measurement, event_type)")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS quota_daily (
                        day_key TEXT PRIMARY KEY,
                        measured_saved INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                """)
                # One-time backfill for existing DBs so pre-upgrade 30d measured usage is captured
                row_qd = cursor.execute("SELECT COUNT(*) FROM quota_daily").fetchone()
                if row_qd and int(row_qd[0]) == 0:
                    cutoff_30 = (datetime.now() - timedelta(days=30)).isoformat()
                    cursor.execute("""
                        INSERT OR IGNORE INTO quota_daily (day_key, measured_saved, updated_at)
                        SELECT substr(timestamp, 1, 10), COALESCE(SUM(saved_tokens), 0), MAX(timestamp)
                        FROM events
                        WHERE timestamp >= ? AND measurement = 'measured' AND saved_tokens > 0
                        GROUP BY substr(timestamp, 1, 10)
                    """, (cutoff_30,))
                try:
                    cursor.execute("PRAGMA user_version = 2;")
                except sqlite3.Error:
                    pass
            _INITIALIZED_METRICS_DBS.add(self.db_path)

    def _quota_mirror_path(self) -> str:
        """Returns persistent sidecar ledger path (kept outside data/metrics.db for default installs)."""
        if os.environ.get("TOKENSAFE_DB_PATH") or os.environ.get("TOKENSAFE_DATA_DIR"):
            return os.path.join(os.path.dirname(self.db_path), ".qledger.json")
        default_data = os.path.abspath(resolve_data_dir())
        if os.path.abspath(os.path.dirname(self.db_path)) == default_data:
            return os.path.join(os.path.dirname(default_data), ".qledger.json")
        return os.path.join(os.path.dirname(self.db_path), ".qledger.json")

    @staticmethod
    def _qledger_write_atomic(mirror_path: str, data: Dict[str, Any]) -> None:
        parent = os.path.dirname(mirror_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp_p = f"{mirror_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_p, mirror_path)

    @staticmethod
    def _qledger_locked(mirror_path: str, mutate_fn):
        parent = os.path.dirname(mirror_path) or "."
        os.makedirs(parent, exist_ok=True)
        lock_path = mirror_path + ".lock"
        fh = open(lock_path, "a+")
        locked_posix = False
        locked_win = False
        try:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked_posix = True
            except ImportError:
                try:
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    locked_win = True
                except Exception:
                    pass
            return mutate_fn()
        finally:
            try:
                if locked_posix:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                elif locked_win:
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            fh.close()

    def _sync_quota_mirror(self, day_key: str, delta_measured: int = 0) -> int:
        """Merges quota_daily with a sidecar ledger under file lock so concurrent hooks cannot drop deltas."""
        mirror_path = self._quota_mirror_path()
        cutoff_day = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        def _do():
            data: Dict[str, Any] = {"days": {}, "server_floor": 0, "server_floor_ts": 0}
            try:
                if os.path.exists(mirror_path):
                    with open(mirror_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data.update(loaded)
            except Exception:
                pass
            days = {
                str(k): int(v)
                for k, v in (data.get("days") or {}).items()
                if str(k) >= cutoff_day and int(v or 0) > 0
            }
            if delta_measured > 0 and day_key >= cutoff_day:
                days[day_key] = days.get(day_key, 0) + int(delta_measured)
            data["days"] = days
            if delta_measured > 0:
                try:
                    self._qledger_write_atomic(mirror_path, data)
                except Exception:
                    pass
            local_sum = sum(days.values())
            srv_floor = 0
            try:
                if time.time() - float(data.get("server_floor_ts", 0)) <= 30 * 86400:
                    srv_floor = int(data.get("server_floor", 0) or 0)
            except Exception:
                srv_floor = 0
            return max(local_sum, srv_floor)

        try:
            return self._qledger_locked(mirror_path, _do)
        except Exception:
            return _do()

    def record_server_quota_floor(self, server_monthly_measured: int) -> None:
        """Persists a server-verified 30-day measured quota floor under file lock."""
        if int(server_monthly_measured or 0) <= 0:
            return
        mirror_path = self._quota_mirror_path()

        def _do():
            data: Dict[str, Any] = {"days": {}, "server_floor": 0, "server_floor_ts": 0}
            try:
                if os.path.exists(mirror_path):
                    with open(mirror_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data.update(loaded)
            except Exception:
                pass
            data["server_floor"] = max(int(data.get("server_floor", 0) or 0), int(server_monthly_measured))
            data["server_floor_ts"] = int(time.time())
            try:
                self._qledger_write_atomic(mirror_path, data)
            except Exception:
                pass

        try:
            self._qledger_locked(mirror_path, _do)
        except Exception:
            _do()

    def get_server_quota_floor(self) -> int:
        """Returns the active server-verified measured quota floor from mirror ledger (within 30-day window)."""
        mirror_path = self._quota_mirror_path()
        try:
            if os.path.exists(mirror_path):
                with open(mirror_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if time.time() - float(data.get("server_floor_ts", 0)) <= 30 * 86400:
                        return int(data.get("server_floor", 0) or 0)
        except Exception:
            pass
        return 0

    def record_event(
        self,
        session_id: str,
        event_type: str,
        original_tokens: int,
        optimized_tokens: int,
        details: Optional[Dict[str, Any]] = None,
        measurement: Optional[str] = None
    ) -> Dict[str, Any]:
        """Records an optimization event and updates session totals.

        `measurement` labels the honesty of `saved_tokens`:
          'measured'  - literally metered I/O reduction (tool output was smaller)
          'estimated' - counterfactual baseline (see ESTIMATED_EVENT_TYPES / METRICS.md)
        When omitted, the label is derived from the event type.
        """
        measurement_label = resolve_measurement(event_type, measurement)
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
                    optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details,
                    measurement
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, now_iso, event_type, original_tokens,
                  optimized_tokens, saved_tokens, round(reduction_pct, 2), est_usd, details_json,
                  measurement_label))

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

            # 3. Monotonic 30-day measured quota ledger (never eroded by >5k event pruning)
            if measurement_label == "measured" and saved_tokens > 0:
                day_key = now_iso[:10]
                cursor.execute("""
                    INSERT INTO quota_daily (day_key, measured_saved, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(day_key) DO UPDATE SET
                        measured_saved = quota_daily.measured_saved + excluded.measured_saved,
                        updated_at = excluded.updated_at
                """, (day_key, saved_tokens, now_iso))

        if measurement_label == "measured" and saved_tokens > 0:
            self._sync_quota_mirror(now_iso[:10], saved_tokens)

        # Wall-clock auto-retention (marker-file gated, cross-process safe)
        self._maybe_autoprune(self.db_path)

        return {
            "saved_tokens": saved_tokens,
            "reduction_pct": round(reduction_pct, 1),
            "est_usd_saved": est_usd,
            "measurement": measurement_label
        }

    @staticmethod
    def _infer_agent(session_id: str, details: Dict[str, Any]) -> str:
        """Best-effort attribution of an event to the agent that produced it.
        Explicit details.agent (new adapters) wins; session_id prefixes cover
        the historic naming conventions."""
        explicit = details.get("agent")
        if explicit:
            return str(explicit)
        sid = session_id or ""
        if sid == "mcp_session":
            return "mcp"
        if sid == "proxy_session":
            return "proxy"
        if sid.startswith("hook_"):
            return sid[len("hook_"):] or "hook"
        if sid.startswith("mcp:"):
            return sid[4:] or "mcp"
        return "antigravity"

    def get_by_agent(self) -> Dict[str, Dict[str, Any]]:
        """Per-agent savings breakdown for the honest Cumulative Total:
        aggregates events by the agent that produced them, with the
        measured/estimated split (see METRICS.md)."""
        by_agent: Dict[str, Dict[str, Any]] = {}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT session_id, saved_tokens, est_usd_saved, measurement, details
                    FROM events
                """)
                for row in cursor.fetchall():
                    try:
                        details = json.loads(row["details"] or "{}")
                        if not isinstance(details, dict):
                            details = {}
                    except Exception:
                        details = {}
                    agent = self._infer_agent(row["session_id"], details)
                    bucket = by_agent.setdefault(agent, {
                        "events": 0, "saved_tokens": 0, "saved_tokens_measured": 0,
                        "saved_tokens_estimated": 0, "est_usd_saved": 0.0,
                    })
                    saved = int(row["saved_tokens"] or 0)
                    measurement = row["measurement"] or "measured"
                    bucket["events"] += 1
                    bucket["saved_tokens"] += saved
                    bucket["saved_tokens_measured"] += saved if measurement == "measured" else 0
                    bucket["saved_tokens_estimated"] += saved if measurement == "estimated" else 0
                    bucket["est_usd_saved"] = round(bucket["est_usd_saved"] + float(row["est_usd_saved"] or 0.0), 4)
        except Exception:
            pass
        # Largest contributor first
        return dict(sorted(by_agent.items(), key=lambda kv: kv[1]["saved_tokens"], reverse=True))

    def get_weekly_saved(self, days: int = 7) -> Dict[str, Any]:
        """Rolling-window savings totals for the quick `tokensafe status` view.
        Reads raw events only (lifetime rollups have no timestamp and are
        deliberately excluded — this is the recent-activity signal)."""
        days = max(1, int(days))
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            with self._get_connection() as conn:
                row = conn.cursor().execute(
                    """
                    SELECT COUNT(*) AS events,
                           COALESCE(SUM(saved_tokens), 0) AS saved_tokens,
                           COALESCE(SUM(est_usd_saved), 0.0) AS usd_saved
                    FROM events
                    WHERE timestamp >= ? AND saved_tokens > 0
                    """, (cutoff,)
                ).fetchone()
                return {
                    "events": int(row["events"] or 0),
                    "saved_tokens": int(row["saved_tokens"] or 0),
                    "usd_saved": round(float(row["usd_saved"] or 0.0), 4),
                }
        except Exception:
            return {"events": 0, "saved_tokens": 0, "usd_saved": 0.0}

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
                    COALESCE(SUM(est_usd_saved), 0.0) as active_usd,
                    COALESCE(SUM(CASE WHEN measurement = 'measured' THEN saved_tokens ELSE 0 END), 0) as active_saved_measured,
                    COALESCE(SUM(CASE WHEN measurement = 'estimated' THEN saved_tokens ELSE 0 END), 0) as active_saved_estimated
                FROM events
            """)
            ev_row = cursor.fetchone()

            cursor.execute("SELECT COUNT(DISTINCT session_id) as session_count FROM sessions")
            sess_row = cursor.fetchone()
            session_count = sess_row["session_count"] if sess_row else 0

            # 3. Breakdown of active events
            cursor.execute("""
                SELECT event_type, COUNT(*) as count, COALESCE(SUM(saved_tokens), 0) as saved,
                       COALESCE(SUM(CASE WHEN measurement = 'estimated' THEN saved_tokens ELSE 0 END), 0) as estimated_saved
                FROM events
                GROUP BY event_type
            """)
            combined_breakdown = dict(life_breakdown)
            for r in cursor.fetchall():
                ev_type = r["event_type"]
                if ev_type not in combined_breakdown:
                    combined_breakdown[ev_type] = {"count": 0, "saved": 0, "estimated_saved": 0}
                else:
                    combined_breakdown[ev_type] = dict(combined_breakdown[ev_type])
                    combined_breakdown[ev_type].setdefault("estimated_saved", 0)
                combined_breakdown[ev_type]["count"] += r["count"]
                combined_breakdown[ev_type]["saved"] += r["saved"]
                combined_breakdown[ev_type]["estimated_saved"] += r["estimated_saved"]

        total_events = life_events + (ev_row["active_events"] if ev_row else 0)
        total_orig = life_orig + (ev_row["active_orig"] if ev_row else 0)
        total_opt = life_opt + (ev_row["active_opt"] if ev_row else 0)
        total_saved = life_saved + (ev_row["active_saved"] if ev_row else 0)
        total_usd = life_usd + (ev_row["active_usd"] if ev_row else 0.0)
        overall_reduction = (total_saved / total_orig * 100.0) if total_orig > 0 else 0.0

        # Honesty split: lifetime rollup breakdown carries per-type estimated mass
        # (see prune); active events are split directly by the measurement column.
        life_estimated = sum(int(v.get("estimated_saved", 0) or 0) for v in life_breakdown.values())
        saved_measured = (ev_row["active_saved_measured"] if ev_row else 0) + (
            life_saved - life_estimated
        )
        saved_estimated = (ev_row["active_saved_estimated"] if ev_row else 0) + life_estimated

        return {
            "session_count": max(session_count, 1 if total_events > 0 else 0),
            "total_events": total_events,
            "total_original_tokens": total_orig,
            "total_optimized_tokens": total_opt,
            "total_saved_tokens": total_saved,
            "total_saved_tokens_measured": saved_measured,
            "total_saved_tokens_estimated": saved_estimated,
            "overall_reduction_pct": round(overall_reduction, 1),
            "total_usd_saved": round(total_usd, 4),
            "total_usd_saved_approx": True,  # USD is a derived estimate (~$1.5/M blended), never a bill
            "by_agent": self.get_by_agent(),
            "breakdown": combined_breakdown
        }

    def get_monthly_saved_tokens(self, measured_only: bool = True) -> int:
        """Returns trailing 30-day tokens saved for quota calculations (measured-only by default).

        Reconciles live `events`, the un-prunable `quota_daily` table, and the
        persistent sidecar ledger so neither >5,000-row auto-pruning nor deleting
        `metrics.db` drops counted Community usage.
        """
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        thirty_days_day = thirty_days_ago[:10]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if measured_only:
                cursor.execute("""
                    SELECT COALESCE(SUM(saved_tokens), 0) as monthly_saved
                    FROM events
                    WHERE timestamp >= ? AND measurement = 'measured'
                """, (thirty_days_ago,))
                ev_sum = int(cursor.fetchone()["monthly_saved"])
                cursor.execute("""
                    SELECT COALESCE(SUM(measured_saved), 0) as daily_saved
                    FROM quota_daily
                    WHERE day_key >= ?
                """, (thirty_days_day,))
                daily_sum = int(cursor.fetchone()["daily_saved"])
                mirror_sum = self._sync_quota_mirror(thirty_days_day, 0)
                return max(ev_sum, daily_sum, mirror_sum)
            else:
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
                       optimized_tokens, saved_tokens, reduction_pct, est_usd_saved, details,
                       measurement
                FROM events
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

        events = []
        for r in rows:
            ev = dict(r)
            try:
                parsed = json.loads(ev["details"]) if ev.get("details") else {}
                ev["details"] = parsed if isinstance(parsed, dict) else {}
            except Exception:
                ev["details"] = {}
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
                parsed = json.loads(res["details"]) if res.get("details") else {}
                res["details"] = parsed if isinstance(parsed, dict) else {}
            except Exception:
                res["details"] = {}
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
                # 2. Breakdown of pruned events (with estimated share for the honesty split)
                cursor.execute(f"""
                    SELECT event_type, COUNT(*) as cnt, COALESCE(SUM(saved_tokens), 0) as saved,
                           COALESCE(SUM(CASE WHEN measurement = 'estimated' THEN saved_tokens ELSE 0 END), 0) as est_saved
                    FROM events
                    WHERE {combined_where}
                    GROUP BY event_type
                """, params)
                pruned_breakdown = {
                    r["event_type"]: {"count": r["cnt"], "saved": r["saved"], "estimated_saved": r["est_saved"]}
                    for r in cursor.fetchall()
                }

                # 3. Fetch existing lifetime aggregates
                cursor.execute("SELECT * FROM lifetime_aggregates WHERE id = 1")
                life_row = cursor.fetchone()
                current_breakdown = json.loads(life_row["breakdown"] or "{}") if life_row else {}

                for ev_type, vals in pruned_breakdown.items():
                    if ev_type not in current_breakdown:
                        current_breakdown[ev_type] = {"count": 0, "saved": 0, "estimated_saved": 0}
                    else:
                        current_breakdown[ev_type] = dict(current_breakdown[ev_type])
                        current_breakdown[ev_type].setdefault("estimated_saved", 0)
                    current_breakdown[ev_type]["count"] += vals["count"]
                    current_breakdown[ev_type]["saved"] += vals["saved"]
                    current_breakdown[ev_type]["estimated_saved"] += vals["estimated_saved"]

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

        cutoff_qd = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        cutoff_banner = int(time.time()) - 30 * 86400
        with self._get_connection() as conn:
            conn.execute("DELETE FROM quota_daily WHERE day_key < ?", (cutoff_qd,))
            try:
                conn.execute("DELETE FROM quota_banner_state WHERE updated_at < ?", (cutoff_banner,))
            except sqlite3.OperationalError:
                pass

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
