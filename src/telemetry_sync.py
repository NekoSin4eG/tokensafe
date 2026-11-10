"""
TokenSafe Telemetry Sync Engine.
Synchronizes local token optimization metrics from SQLite (data/metrics.db)
with TokenSafe Cloud Dashboard API.

Features:
- Zero external runtime dependencies (Python standard library only).
- Offline-safe with fast connection timeout (< 2.5s).
- Non-blocking asynchronous background sync support.
- Idempotent tracking of synced event IDs to prevent duplicate counting.
- Lifetime aggregate preservation for pruned historical events.
"""

import os
import sys
import json
import time
import sqlite3
import threading
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.license_manager import LicenseManager
from src.metrics import resolve_measurement

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
METRICS_DB_PATH = os.path.join(DATA_DIR, "metrics.db")
SYNC_STATE_PATH = os.path.join(DATA_DIR, "sync_state.json")
SYNC_LOCK_PATH = os.path.join(DATA_DIR, ".sync.lock")


def _get_cloud_url() -> str:
    base = os.environ.get("TOKENSAFE_CLOUD_URL")
    if not base:
        base = "https://api.tokensafe.dev"
    return base.rstrip("/")


def _telemetry_disabled() -> bool:
    """TOKENSAFE_TELEMETRY_DISABLED=1 fully turns off cloud telemetry (privacy / offline / CI)."""
    return os.environ.get("TOKENSAFE_TELEMETRY_DISABLED", "").strip().lower() in ("1", "true", "yes")


class TelemetrySync:
    """Manages secure telemetry synchronization between local metrics and cloud dashboard."""

    _sync_lock = threading.Lock()
    _last_bg_sync_time = 0.0

    @classmethod
    def get_state(cls) -> Dict[str, Any]:
        """Loads sync cursor state from persistent JSON."""
        if not os.path.exists(SYNC_STATE_PATH):
            return {
                "last_synced_event_id": 0,
                "last_synced_at": 0,
                "total_synced_events": 0,
                "lifetime_rollup_synced": False
            }
        try:
            with open(SYNC_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "last_synced_event_id": 0,
                "last_synced_at": 0,
                "total_synced_events": 0,
                "lifetime_rollup_synced": False
            }

    @classmethod
    def save_state(cls, state: Dict[str, Any]):
        """Persists sync cursor state."""
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(SYNC_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    @classmethod
    def get_sync_status(cls) -> Dict[str, Any]:
        """Returns statistics on local events, synced events, and pending sync count."""
        state = cls.get_state()
        last_id = state.get("last_synced_event_id", 0)

        total_local = 0
        pending = 0
        if os.path.exists(METRICS_DB_PATH):
            try:
                conn = sqlite3.connect(METRICS_DB_PATH)
                c = conn.cursor()
                row = c.execute("SELECT COUNT(*) FROM events").fetchone()
                total_local = row[0] if row else 0
                row_pending = c.execute("SELECT COUNT(*) FROM events WHERE id > ?", (last_id,)).fetchone()
                pending = row_pending[0] if row_pending else 0
                conn.close()
            except Exception:
                pass

        return {
            "total_local_events": total_local,
            "last_synced_event_id": last_id,
            "pending_events": pending,
            "last_synced_at": state.get("last_synced_at", 0),
            "total_synced_events": state.get("total_synced_events", 0)
        }

    @classmethod
    def sync(
        cls,
        cloud_url: Optional[str] = None,
        license_key: Optional[str] = None,
        email: Optional[str] = None,
        force: bool = False,
        batch_size: int = 200,
        timeout: float = 10.0,
        verbose: bool = False
    ) -> Dict[str, Any]:
        """
        Synchronizes all un-synced events from data/metrics.db to the TokenSafe cloud server.
        Returns a dict with sync results and totals.
        """
        if _telemetry_disabled():
            return {
                "success": True,
                "synced_events": 0,
                "total_synced": 0,
                "message": "Telemetry disabled via TOKENSAFE_TELEMETRY_DISABLED."
            }
        with cls._sync_lock:
            base_url = (cloud_url or _get_cloud_url()).rstrip("/")
            target_url = f"{base_url}/api/telemetry/sync"
            active_key = license_key or LicenseManager.get_active_license_key()

            target_email = (email or "").strip().lower()
            if not target_email and active_key:
                _, payload, _ = LicenseManager.validate_license(active_key)
                target_email = payload.get("email", "").strip().lower()

            # Community fallback: unauthenticated users participate in global token savings
            if not target_email:
                target_email = "community@tokensafe.dev"
            if not active_key:
                active_key = "community"

            if not os.path.exists(METRICS_DB_PATH):
                return {
                    "success": True,
                    "synced_events": 0,
                    "total_synced": 0,
                    "message": "No local metrics database found."
                }

            state = cls.get_state()
            last_id = 0 if force else state.get("last_synced_event_id", 0)

            try:
                conn = sqlite3.connect(METRICS_DB_PATH)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                synthetic_events: List[Dict[str, Any]] = []
                if not state.get("lifetime_rollup_synced", False):
                    try:
                        life_row = cur.execute("SELECT * FROM lifetime_aggregates WHERE id = 1").fetchone()
                        if life_row and life_row["total_saved_tokens"] > 0:
                            synthetic_events.append({
                                "event_type": "ROLLUP_LIFETIME",
                                "original_tokens": int(life_row["total_original_tokens"]),
                                "saved_tokens": int(life_row["total_saved_tokens"]),
                                "usd_saved": float(life_row["total_usd_saved"]),
                                "model": "aggregate",
                                "timestamp": int(time.time()),
                                # Rolled-up historical mass of mixed provenance: conservatively estimated.
                                "measurement": "estimated"
                            })
                    except Exception:
                        pass

                # The measurement column may be absent on legacy databases that have
                # not yet been migrated by MetricsTracker._init_db; fall back to the
                # event-type map so sync never blocks on schema drift.
                try:
                    rows = cur.execute(
                        "SELECT id, timestamp, event_type, original_tokens, saved_tokens, est_usd_saved, details, measurement "
                        "FROM events WHERE id > ? ORDER BY id ASC LIMIT 2000",
                        (last_id,)
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = cur.execute(
                        "SELECT id, timestamp, event_type, original_tokens, saved_tokens, est_usd_saved, details, "
                        "NULL as measurement "
                        "FROM events WHERE id > ? ORDER BY id ASC LIMIT 2000",
                        (last_id,)
                    ).fetchall()
                conn.close()
            except Exception as e:
                return {
                    "success": False,
                    "synced_events": 0,
                    "error": f"Failed to read local metrics database: {str(e)}"
                }

            if not rows and not synthetic_events:
                return {
                    "success": True,
                    "synced_events": 0,
                    "total_synced": state.get("total_synced_events", 0),
                    "message": "All metrics are already up to date."
                }

            formatted_items: List[Tuple[Optional[int], Dict[str, Any]]] = [
                (None, ev) for ev in synthetic_events
            ]
            current_last_id = last_id

            for r in rows:
                ev_id = int(r["id"])
                raw_ts = r["timestamp"]
                ts_int = int(time.time())
                if isinstance(raw_ts, (int, float)):
                    ts_int = int(raw_ts)
                elif isinstance(raw_ts, str) and raw_ts.strip():
                    try:
                        ts_int = int(float(raw_ts.strip()))
                    except ValueError:
                        try:
                            clean_iso = raw_ts.strip().replace("Z", "+00:00")
                            dt = datetime.fromisoformat(clean_iso)
                            ts_int = int(dt.timestamp())
                        except Exception:
                            ts_int = int(time.time())

                model_name = os.environ.get("AGY_MODEL") or os.environ.get("MODEL") or "gemini-3.8-flash"
                if r["details"]:
                    try:
                        det = json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]
                        if isinstance(det, dict) and det.get("model"):
                            model_name = str(det["model"])
                    except Exception:
                        pass

                formatted_items.append((ev_id, {
                    "event_type": str(r["event_type"] or "PROMPT"),
                    "original_tokens": int(r["original_tokens"] or 0),
                    "saved_tokens": int(r["saved_tokens"] or 0),
                    "usd_saved": float(r["est_usd_saved"] or 0.0),
                    "model": model_name,
                    "timestamp": ts_int,
                    "measurement": resolve_measurement(str(r["event_type"] or "PROMPT"), r["measurement"])
                }))

            total_sent = 0
            cumulative_synced = state.get("total_synced_events", 0)
            rollup_synced = bool(state.get("lifetime_rollup_synced", False))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe-Sync/1.7.0"
            }
            if active_key:
                headers["Authorization"] = f"Bearer {active_key}"

            def _record_failure(err_msg: str) -> Dict[str, Any]:
                fail_state = {
                    "last_synced_event_id": current_last_id,
                    "last_synced_at": state.get("last_synced_at", 0) if total_sent == 0 else int(time.time()),
                    "last_attempt_at": int(time.time()),
                    "total_synced_events": cumulative_synced,
                    "lifetime_rollup_synced": rollup_synced
                }
                cls.save_state(fail_state)
                return {
                    "success": False,
                    "synced_events": total_sent,
                    "error": err_msg
                }

            for i in range(0, len(formatted_items), batch_size):
                batch_items = formatted_items[i:i + batch_size]
                chunk = [item[1] for item in batch_items]
                body_dict = {
                    "email": target_email,
                    "license_key": active_key,
                    "events": chunk
                }
                body_bytes = json.dumps(body_dict).encode("utf-8")

                req = urllib.request.Request(target_url, data=body_bytes, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        if resp.status == 200:
                            total_sent += len(chunk)
                            cumulative_synced += len(chunk)
                            for ev_id, ev_payload in batch_items:
                                if ev_id is not None and ev_id > current_last_id:
                                    current_last_id = ev_id
                                if ev_payload.get("event_type") == "ROLLUP_LIFETIME":
                                    rollup_synced = True
                            if not synthetic_events:
                                rollup_synced = True
                            cls.save_state({
                                "last_synced_event_id": current_last_id,
                                "last_synced_at": int(time.time()),
                                "last_attempt_at": int(time.time()),
                                "total_synced_events": cumulative_synced,
                                "lifetime_rollup_synced": rollup_synced
                            })
                        else:
                            return _record_failure(f"Server returned HTTP {resp.status}")
                except urllib.error.HTTPError as he:
                    return _record_failure(f"Cloud API error: {he.code} {he.reason}")
                except Exception as ex:
                    return _record_failure(f"Network error during sync: {str(ex)}")

            return {
                "success": True,
                "synced_events": total_sent,
                "total_synced": cumulative_synced,
                "message": f"Successfully synchronized {total_sent:,} events to TokenSafe Cloud."
            }

    @classmethod
    def sync_background(cls, throttle_seconds: float = 60.0, force: bool = False):
        """
        Spawns a detached, non-blocking background OS process to sync metrics.
        Throttled to run at most once every `throttle_seconds` (default: 60s)
        using persistent state from sync_state.json.
        Detached from parent process group so it survives the termination of short-lived CLI lifecycle hooks.
        """
        if _telemetry_disabled():
            return
        now = time.time()
        state = cls.get_state()
        last_synced = max(state.get("last_synced_at", 0), state.get("last_attempt_at", 0))
        if not force and (now - last_synced) < throttle_seconds:
            return

        status = cls.get_sync_status()
        if status.get("pending_events", 0) == 0:
            return

        # Prevent overlapping sync runs via PID lock file.
        # Atomic O_EXCL claim closes the check-then-write race where two hook
        # processes could both pass the liveness check and spawn duplicate syncs.
        lock_claimed = False
        for _ in range(2):
            try:
                fd = os.open(SYNC_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
                lock_claimed = True
                break
            except FileExistsError:
                try:
                    with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                        pid = int(f.read().strip())
                    # Check if PID is still alive
                    os.kill(pid, 0)
                    return  # an active sync owns the lock
                except (ValueError, OSError, ProcessLookupError):
                    # Stale lock from a dead process: reclaim it once
                    try:
                        os.unlink(SYNC_LOCK_PATH)
                    except OSError:
                        return
        if not lock_claimed:
            return

        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            script_path = os.path.abspath(__file__)
            subprocess.Popen(
                [sys.executable, script_path, "--background"],
                cwd=PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
                creationflags=flags
            )
        except Exception:
            pass


if __name__ == "__main__":
    is_bg = "--background" in sys.argv
    os.makedirs(DATA_DIR, exist_ok=True)
    # Claim the lock atomically; if another live process owns it, exit without syncing.
    try:
        fd = os.open(SYNC_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        try:
            with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            sys.exit(0)  # live owner is syncing already
        except (ValueError, OSError, ProcessLookupError):
            try:
                os.unlink(SYNC_LOCK_PATH)
            except OSError:
                sys.exit(0)
            try:
                fd = os.open(SYNC_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
            except FileExistsError:
                sys.exit(0)
    try:
        res = TelemetrySync.sync(timeout=10.0, verbose=not is_bg)
        if not is_bg:
            print(json.dumps(res, indent=2))
    finally:
        try:
            if os.path.exists(SYNC_LOCK_PATH):
                os.remove(SYNC_LOCK_PATH)
        except Exception:
            pass
