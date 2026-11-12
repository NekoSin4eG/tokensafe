"""Live relay: ships anonymized proxy events to the TokenSafe cloud live view.

Only metadata ever leaves the machine — saved tokens, USD estimate, model name
and a machine hash. No prompts, no file paths, no content. Batches flush at
most every TOKENSAFE_LIVE_FLUSH_SECONDS (default 5s) from a daemon thread; a
license-less (Community) install never transmits.
"""

import hashlib
import json
import os
import threading
import time
import urllib.request
from collections import deque
from typing import Dict

from src.license_manager import LicenseManager

_MACHINE_HASH = ""


def _machine_hash() -> str:
    global _MACHINE_HASH
    if _MACHINE_HASH:
        return _MACHINE_HASH
    try:
        import socket
        uid = os.getuid() if hasattr(os, "getuid") else 0
        raw = f"{socket.gethostname()}|{uid}"
    except Exception:
        raw = "tokensafe"
    _MACHINE_HASH = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]
    return _MACHINE_HASH


class LiveRelay:
    """Records anonymized live events and flushes bounded batches to the cloud."""

    RING_MAX = 600
    FLUSH_SECONDS = float(os.environ.get("TOKENSAFE_LIVE_FLUSH_SECONDS", "5"))

    def __init__(self):
        self._ring: deque = deque(maxlen=self.RING_MAX)
        self._lock = threading.Lock()
        self._last_flush = time.time()  # grace window before the first flush
        self._flushing = False

    def record(self, event_type: str, data: Dict) -> None:
        """Queues a whitelisted, clamped event; flushes when the window elapses."""
        try:
            ev = {
                "type": str(event_type or "session")[:40],
                "saved_tokens": min(10_000_000, max(0, int(data.get("saved_tokens") or data.get("tokens_saved") or 0))),
                "usd_saved": min(10000.0, max(0.0, float(data.get("usd_saved") or data.get("usd") or 0.0))),
                "model": str(data.get("model") or "")[:40],
                "machine": str(data.get("machine") or _machine_hash())[:64],
                "ts": time.time(),
            }
        except Exception:
            return
        with self._lock:
            self._ring.append(ev)
            due = bool(self._ring) and (time.time() - self._last_flush) >= self.FLUSH_SECONDS
        if due:
            self.flush_async()

    def flush_async(self) -> None:
        if self._flushing:
            return
        self._flushing = True
        threading.Thread(target=self._flush, daemon=True).start()

    def _flush(self) -> None:
        try:
            with self._lock:
                batch = list(self._ring)
                self._ring.clear()
                self._last_flush = time.time()
            if not batch:
                return
            key = ""
            try:
                key = LicenseManager.get_active_license_key() or ""
            except Exception:
                key = ""
            if not key:
                return  # Community installs never transmit live events
            base = os.environ.get("TOKENSAFE_CLOUD_URL", "").rstrip("/") or "https://api.tokensafe.dev"
            url = os.environ.get("TOKENSAFE_LIVE_URL", f"{base}/api/live/relay")
            body = json.dumps({"license_key": key, "machine": _machine_hash(), "events": batch[:100]}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "TokenSafe-LiveRelay/2.8",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                resp.read()
        except Exception:
            pass  # live telemetry is best-effort and must never block the proxy
        finally:
            self._flushing = False


LIVE_RELAY = LiveRelay()
