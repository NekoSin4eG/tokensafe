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
METRICS_DB_PATH = os.environ.get("TOKENSAFE_DB_PATH") or os.path.join(DATA_DIR, "metrics.db")
SYNC_STATE_PATH = os.path.join(DATA_DIR, "sync_state.json")
SYNC_LOCK_PATH = os.path.join(DATA_DIR, ".sync.lock")
_DEFAULT_DATA_DIR = DATA_DIR
_DEFAULT_METRICS_DB_PATH = METRICS_DB_PATH
_DEFAULT_SYNC_STATE_PATH = SYNC_STATE_PATH
_DEFAULT_SYNC_LOCK_PATH = SYNC_LOCK_PATH


def _data_dir() -> str:
    if DATA_DIR != _DEFAULT_DATA_DIR:
        return DATA_DIR
    return resolve_data_dir()


def _metrics_db_path() -> str:
    if METRICS_DB_PATH != _DEFAULT_METRICS_DB_PATH:
        return METRICS_DB_PATH
    explicit = os.environ.get("TOKENSAFE_DB_PATH", "").strip()
    if explicit:
        return explicit
    return os.path.join(_data_dir(), "metrics.db")


def _sync_state_path() -> str:
    if SYNC_STATE_PATH != _DEFAULT_SYNC_STATE_PATH:
        return SYNC_STATE_PATH
    return os.path.join(_data_dir(), "sync_state.json")


def _sync_lock_path() -> str:
    if SYNC_LOCK_PATH != _DEFAULT_SYNC_LOCK_PATH:
        return SYNC_LOCK_PATH
    return os.path.join(_data_dir(), ".sync.lock")


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
        state_path = _sync_state_path()
        if not os.path.exists(state_path):
            return {
                "last_synced_event_id": 0,
                "last_synced_at": 0,
                "total_synced_events": 0,
                "lifetime_rollup_synced": False
            }
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {
                "last_synced_event_id": 0,
                "last_synced_at": 0,
                "total_synced_events": 0,
                "lifetime_rollup_synced": False
            }
        except Exception:
            return {
                "last_synced_event_id": 0,
                "last_synced_at": 0,
                "total_synced_events": 0,
                "lifetime_rollup_synced": False
            }

    @classmethod
    def save_state(cls, state: Dict[str, Any]):
        """Persists sync cursor state atomically."""
        state_path = _sync_state_path()
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        try:
            tmp_path = f"{state_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, state_path)
        except Exception:
            pass

    @classmethod
    def get_sync_status(cls) -> Dict[str, Any]:
        """Returns statistics on local events, synced events, and pending sync count."""
        state = cls.get_state()
        last_id = state.get("last_synced_event_id", 0)

        total_local = 0
        pending = 0
        db_path = _metrics_db_path()
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
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
            if active_key and not active_key.startswith("ts_live_"):
                is_valid, payload, _ = LicenseManager.validate_license(active_key)
                if not is_valid:
                    # Expired/revoked license must downgrade to Community sync instead of getting 401 forever
                    active_key = ""
                    if not target_email:
                        target_email = ""
                elif not target_email:
                    target_email = str(payload.get("email", "")).strip().lower()

            # Community fallback: unauthenticated or expired users participate in global community sync
            if not target_email:
                target_email = "community@tokensafe.dev"
            if not active_key:
                active_key = "community"

            db_path = _metrics_db_path()
            if not os.path.exists(db_path):
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
                local_monthly_measured = MetricsTracker(db_path=db_path).get_monthly_saved_tokens(measured_only=True)
            except Exception:
                local_monthly_measured = 0
            machine_fp = "" if os.environ.get("TOKENSAFE_TELEMETRY_DISABLED") in ("1", "true", "yes") else LicenseManager.get_machine_fingerprint()

            total_sent = 0
            cumulative_synced = state.get("total_synced_events", 0)
            rollup_synced = bool(state.get("lifetime_rollup_synced", False))
            current_last_id = last_id
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe-Sync/3.1.1"
            }
            if active_key:
                headers["Authorization"] = f"Bearer {active_key}"

            def _record_failure(err_msg: str) -> Dict[str, Any]:
                fail_state = {
                    "last_synced_event_id": current_last_id,
                    "last_synced_at": state.get("last_synced_at", 0) if total_sent == 0 else int(time.time()),
                    "last_attempt_at": int(time.time()),
                    "last_quota_sync_at": state.get("last_quota_sync_at", 0),
                    "consecutive_failures": int(state.get("consecutive_failures", 0) or 0) + 1,
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
                    conn = sqlite3.connect(db_path)
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
                                        MetricsTracker(db_path=db_path).record_server_quota_floor(srv_measured)
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
                                    "consecutive_failures": 0,
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
        if os.name == "nt":
            try:
                import ctypes
                SYNCHRONIZE = 0x00100000
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if not handle:
                    return False
                try:
                    code = ctypes.c_ulong()
                    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                    return bool(ok and code.value == 259)  # STILL_ACTIVE
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
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
        failures = int(state.get("consecutive_failures", 0) or 0)
        eff_throttle = throttle_seconds
        if not force and failures > 0:
            eff_throttle = min(300.0, max(throttle_seconds, throttle_seconds * (2 ** min(failures, 4))))
        if not force and (now - last_synced) < eff_throttle:
            return

        status = cls.get_sync_status()
        if status.get("pending_events", 0) == 0:
            return

        lock_path = _sync_lock_path()
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        claim_token = secrets.token_hex(8)
        spawn_marker = f"spawn:{claim_token}:{now:.3f}"
        lock_claimed = False
        for _ in range(2):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, spawn_marker.encode("utf-8"))
                finally:
                    os.close(fd)
                lock_claimed = True
                break
            except FileExistsError:
                try:
                    with open(lock_path, "r", encoding="utf-8") as f:
                        existing = f.read()
                    if cls._lock_is_active(existing, allow_legacy_self=False):
                        return
                except OSError:
                    return
                try:
                    os.unlink(lock_path)
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
                with open(lock_path, "r", encoding="utf-8") as f:
                    if f.read().strip() == spawn_marker:
                        os.unlink(lock_path)
            except OSError:
                pass


def _claim_worker_lock(claim_token: str) -> bool:
    """Claims or takes over .sync.lock for the current sync worker process."""
    lock_path = _sync_lock_path()
    worker_marker = f"worker:{os.getpid()}"
    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, worker_marker.encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    existing = f.read().strip()
            except OSError:
                return False

            if claim_token and existing.startswith(f"spawn:{claim_token}:"):
                tmp_path = f"{lock_path}.{os.getpid()}.tmp"
                try:
                    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    try:
                        os.write(fd, worker_marker.encode("utf-8"))
                    finally:
                        os.close(fd)
                    os.replace(tmp_path, lock_path)
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
                os.unlink(lock_path)
            except OSError:
                return False
    return False


if __name__ == "__main__":
    is_bg = "--background" in sys.argv
    claim_token = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--claim-token="):
            claim_token = arg.split("=", 1)[1].strip()

    lock_path = _sync_lock_path()
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    if not _claim_worker_lock(claim_token):
        sys.exit(0)

    worker_marker = f"worker:{os.getpid()}"
    try:
        res = TelemetrySync.sync(timeout=2.5 if is_bg else 10.0, verbose=not is_bg)
        if not is_bg:
            print(json.dumps(res, indent=2))
    finally:
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                if f.read().strip() == worker_marker:
                    os.remove(lock_path)
        except Exception:
            pass
