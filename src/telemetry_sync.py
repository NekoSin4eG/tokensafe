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
import secrets
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

from src.paths import resolve_data_dir

DATA_DIR = resolve_data_dir()
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
                from src.metrics import MetricsTracker
                local_monthly_measured = MetricsTracker(db_path=METRICS_DB_PATH).get_monthly_saved_tokens(measured_only=True)
            except Exception:
                local_monthly_measured = 0
            machine_fp = "" if os.environ.get("TOKENSAFE_TELEMETRY_DISABLED") in ("1", "true", "yes") else LicenseManager.get_machine_fingerprint()

            total_sent = 0
            cumulative_synced = state.get("total_synced_events", 0)
            rollup_synced = bool(state.get("lifetime_rollup_synced", False))
            current_last_id = last_id
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe-Sync/2.7.0"
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

            page_limit = 2000
            max_pages = 25
            for page_idx in range(max_pages):
                try:
                    conn = sqlite3.connect(METRICS_DB_PATH)
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()

                    synthetic_events: List[Dict[str, Any]] = []
                    if page_idx == 0 and not rollup_synced:
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
                            "FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                            (current_last_id, page_limit)
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = cur.execute(
                            "SELECT id, timestamp, event_type, original_tokens, saved_tokens, est_usd_saved, details, "
                            "NULL as measurement "
                            "FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                            (current_last_id, page_limit)
                        ).fetchall()
                    conn.close()
                except Exception as e:
                    if total_sent > 0:
                        return _record_failure(f"Failed to read local metrics database: {str(e)}")
                    return {
                        "success": False,
                        "synced_events": 0,
                        "error": f"Failed to read local metrics database: {str(e)}"
                    }

                if not rows and not synthetic_events:
                    if page_idx == 0:
                        last_q_sync = float(state.get("last_quota_sync_at", 0) or 0)
                        if not (machine_fp and active_key == "community" and (time.time() - last_q_sync > 3600)):
                            return {
                                "success": True,
                                "synced_events": 0,
                                "total_synced": cumulative_synced,
                                "message": "All metrics are already up to date."
                            }
                        formatted_items = [(None, None)]  # quota-only probe for community tier
                    else:
                        break
                else:
                    formatted_items = [
                        (None, ev) for ev in synthetic_events
                    ]

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

                for i in range(0, len(formatted_items), batch_size):
                    batch_items = formatted_items[i:i + batch_size]
                    chunk = [item[1] for item in batch_items if item[1] is not None]
                    body_dict = {
                        "email": target_email,
                        "license_key": active_key,
                        "events": chunk,
                    }
                    if machine_fp and active_key == "community":
                        body_dict["machine_id"] = machine_fp
                        body_dict["monthly_measured_saved"] = local_monthly_measured
                    body_bytes = json.dumps(body_dict).encode("utf-8")

                    req = urllib.request.Request(target_url, data=body_bytes, headers=headers, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            if resp.status == 200:
                                try:
                                    resp_body = json.loads(resp.read().decode("utf-8") or "{}")
                                    srv_measured = int(resp_body.get("server_monthly_measured_saved") or 0)
                                    if srv_measured > 0:
                                        from src.metrics import MetricsTracker
                                        MetricsTracker(db_path=METRICS_DB_PATH).record_server_quota_floor(srv_measured)
                                except Exception:
                                    pass
                                total_sent += len(chunk)
                                cumulative_synced += len(chunk)
                                for ev_id, ev_payload in batch_items:
                                    if ev_id is not None and ev_id > current_last_id:
                                        current_last_id = ev_id
                                    if ev_payload and ev_payload.get("event_type") == "ROLLUP_LIFETIME":
                                        rollup_synced = True
                                if not synthetic_events:
                                    rollup_synced = True
                                cls.save_state({
                                    "last_synced_event_id": current_last_id,
                                    "last_synced_at": int(time.time()),
                                    "last_attempt_at": int(time.time()),
                                    "last_quota_sync_at": int(time.time()),
                                    "total_synced_events": cumulative_synced,
                                    "lifetime_rollup_synced": rollup_synced
                                })
                            else:
                                return _record_failure(f"Server returned HTTP {resp.status}")
                    except urllib.error.HTTPError as he:
                        return _record_failure(f"Cloud API error: {he.code} {he.reason}")
                    except Exception as ex:
                        return _record_failure(f"Network error during sync: {str(ex)}")

                if len(rows) < page_limit:
                    break

            return {
                "success": True,
                "synced_events": total_sent,
                "total_synced": cumulative_synced,
                "message": f"Successfully synchronized {total_sent:,} events to TokenSafe Cloud."
            }

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    @classmethod
    def _lock_is_active(cls, raw_content: str, allow_legacy_self: bool = False) -> bool:
        """Interprets .sync.lock contents (`spawn:<token>:<ts>`, `worker:<pid>`, or legacy `<pid>`)."""
        text = (raw_content or "").strip()
        if not text:
            return False
        if text.startswith("spawn:"):
            parts = text.split(":")
            try:
                claimed_at = float(parts[2])
            except (IndexError, ValueError):
                return False
            return (time.time() - claimed_at) < 15.0
        if text.startswith("worker:"):
            try:
                pid = int(text.split(":", 1)[1])
            except ValueError:
                return False
            return cls._is_pid_alive(pid)
        try:
            pid = int(text)
        except ValueError:
            return False
        if not allow_legacy_self and pid == os.getpid():
            return False
        return cls._is_pid_alive(pid)

    @classmethod
    def sync_background(cls, throttle_seconds: float = 20.0, force: bool = False):
        """
        Spawns a detached, non-blocking background OS process to sync metrics.
        Throttled to run at most once every `throttle_seconds` (default: 20s)
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

        os.makedirs(DATA_DIR, exist_ok=True)
        # Claim a short-lived spawn handoff token atomically so concurrent hooks
        # don't spawn duplicate workers, while the spawned child replaces the
        # lock with its own `worker:<pid>` instead of deadlocking on parent PID.
        claim_token = secrets.token_hex(8)
        spawn_marker = f"spawn:{claim_token}:{now:.3f}"
        lock_claimed = False
        for _ in range(2):
            try:
                fd = os.open(SYNC_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, spawn_marker.encode("utf-8"))
                finally:
                    os.close(fd)
                lock_claimed = True
                break
            except FileExistsError:
                try:
                    with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                        existing = f.read()
                    if cls._lock_is_active(existing, allow_legacy_self=False):
                        return
                except OSError:
                    return
                # Stale spawn/dead-worker/legacy-self lock: reclaim it once
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
                [sys.executable, script_path, "--background", f"--claim-token={claim_token}"],
                cwd=PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
                creationflags=flags
            )
        except Exception:
            try:
                with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                    if f.read().strip() == spawn_marker:
                        os.unlink(SYNC_LOCK_PATH)
            except OSError:
                pass


def _claim_worker_lock(claim_token: str) -> bool:
    """Claims or takes over .sync.lock for the current sync worker process."""
    worker_marker = f"worker:{os.getpid()}"
    for _ in range(2):
        try:
            fd = os.open(SYNC_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, worker_marker.encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                    existing = f.read().strip()
            except OSError:
                return False

            if claim_token and existing.startswith(f"spawn:{claim_token}:"):
                tmp_path = f"{SYNC_LOCK_PATH}.{os.getpid()}.tmp"
                try:
                    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    try:
                        os.write(fd, worker_marker.encode("utf-8"))
                    finally:
                        os.close(fd)
                    os.replace(tmp_path, SYNC_LOCK_PATH)
                    return True
                except OSError:
                    try:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                    except OSError:
                        pass
                    return False

            if TelemetrySync._lock_is_active(existing, allow_legacy_self=True):
                return False

            try:
                os.unlink(SYNC_LOCK_PATH)
            except OSError:
                return False
    return False


if __name__ == "__main__":
    is_bg = "--background" in sys.argv
    claim_token = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--claim-token="):
            claim_token = arg.split("=", 1)[1].strip()

    os.makedirs(DATA_DIR, exist_ok=True)
    if not _claim_worker_lock(claim_token):
        sys.exit(0)

    worker_marker = f"worker:{os.getpid()}"
    try:
        res = TelemetrySync.sync(timeout=10.0, verbose=not is_bg)
        if not is_bg:
            print(json.dumps(res, indent=2))
    finally:
        try:
            with open(SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
                if f.read().strip() == worker_marker:
                    os.remove(SYNC_LOCK_PATH)
        except Exception:
            pass
