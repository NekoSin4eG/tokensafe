"""
TokenSafe Shared Team Cache Client.
Provides cross-developer prompt distillation and AST skeleton caching for Team tier.
Zero external runtime dependencies (standard library urllib + hashlib).
"""

import os
import json
import time
import hashlib
import threading
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, Dict, Any, Tuple
from src.license_manager import LicenseManager

class TeamCache:
    """Distributed cross-developer cache client for TokenSafe Team tier."""

    DEFAULT_TIMEOUT = float(os.environ.get("TOKENSAFE_CACHE_TIMEOUT", "1.2"))  # Fast 1.2s timeout to prevent blocking agent turns
    FAILURE_COOLDOWN_SECONDS = 120.0
    _cooldown_until: float = 0.0
    _cooldowns: Dict[str, float] = {}
    _cooldown_lock = threading.Lock()

    @classmethod
    def _is_in_cooldown(cls, cloud_url: Optional[str] = None) -> bool:
        now = time.monotonic()
        url = cloud_url or cls._get_cloud_url()
        with cls._cooldown_lock:
            if cls._cooldown_until > now:
                return True
            return cls._cooldowns.get(url, 0.0) > now

    @classmethod
    def _trip_cooldown(cls, cloud_url: Optional[str] = None) -> None:
        url = cloud_url or cls._get_cloud_url()
        until = time.monotonic() + cls.FAILURE_COOLDOWN_SECONDS
        with cls._cooldown_lock:
            cls._cooldowns[url] = until

    @classmethod
    def _clear_cooldown(cls, cloud_url: Optional[str] = None) -> None:
        url = cloud_url or cls._get_cloud_url()
        with cls._cooldown_lock:
            cls._cooldown_until = 0.0
            cls._cooldowns.pop(url, None)

    @classmethod
    def _get_cloud_url(cls) -> str:
        return os.environ.get("TOKENSAFE_CLOUD_URL", "https://api.tokensafe.dev").rstrip("/")

    @classmethod
    def _is_team_tier(cls) -> Tuple[bool, str]:
        """Checks if current active license belongs to a Team tier subscription."""
        key = LicenseManager.get_active_license_key()
        if not key:
            return False, ""

        valid, payload, _ = LicenseManager.validate_license(key)
        if valid and payload.get("tier") in ("team", "enterprise"):
            return True, key
        return False, ""

    @classmethod
    def hash_key(cls, key_text: str) -> str:
        """Returns lowercase SHA-256 hash of normalized text."""
        norm = " ".join((key_text or "").strip().split())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    @classmethod
    def get(
        cls,
        cache_type: str,
        key_text: str,
        timeout: Optional[float] = None,
        lic_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Queries Shared Team Cache for compiled S-Expression or AST Skeleton.
        Returns cached value string or None if miss/disabled/timed out/in cooldown.
        """
        cloud_url = cls._get_cloud_url()
        if cls._is_in_cooldown(cloud_url):
            return None
        if not lic_key:
            is_team, resolved_key = cls._is_team_tier()
            if not is_team:
                return None
            lic_key = resolved_key
        if not cache_type or not key_text:
            return None

        h = cls.hash_key(key_text)
        endpoint = f"{cloud_url}/api/team/cache?type={urllib.parse.quote(cache_type)}&hash={h}"

        req = urllib.request.Request(
            endpoint,
            headers={
                "Authorization": f"Bearer {lic_key}",
                "User-Agent": "TokenSafe-TeamCache/1.0"
            }
        )

        eff_timeout = cls.DEFAULT_TIMEOUT if timeout is None else max(0.05, float(timeout))
        try:
            with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    val = data.get("value") or data.get("cached_value")
                    cls._clear_cooldown(cloud_url)
                    if data.get("hit") and val:
                        return val
        except urllib.error.HTTPError as he:
            # Trip circuit breaker on auth rejection or server 5xx outage
            if he.code in (401, 403) or he.code >= 500:
                cls._trip_cooldown(cloud_url)
            return None
        except Exception:
            # Circuit-break offline/unreachable cloud calls so misses don't stall repeated prompts
            cls._trip_cooldown(cloud_url)
            return None

        return None

    @classmethod
    def put(
        cls,
        cache_type: str,
        key_text: str,
        value: str,
        tokens_saved: int = 0,
        lic_key: Optional[str] = None,
    ) -> None:
        """
        Asynchronously broadcasts a freshly compiled distillation or skeleton to the Team Cache.
        Runs in a detached daemon thread so developer workflows never wait on network I/O.
        """
        cloud_url = cls._get_cloud_url()
        if cls._is_in_cooldown(cloud_url):
            return
        if not lic_key:
            is_team, resolved_key = cls._is_team_tier()
            if not is_team:
                return
            lic_key = resolved_key
        if not cache_type or not key_text or not value:
            return

        safe_value = value
        try:
            from src.secret_redactor import SecretRedactor
            safe_value, _, _ = SecretRedactor.redact_text(value)
        except Exception:
            pass

        h = cls.hash_key(key_text)

        def _worker():
            payload = {
                "type": cache_type,
                "hash": h,
                "value": safe_value,
                "tokens_saved": max(0, int(tokens_saved or 0)),
                "license_key": lic_key
            }
            endpoint = f"{cloud_url}/api/team/cache"
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {lic_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "TokenSafe-TeamCache/1.0"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=1.5):
                    cls._clear_cooldown(cloud_url)
            except urllib.error.HTTPError as he:
                if he.code in (401, 403) or he.code >= 500:
                    cls._trip_cooldown(cloud_url)
            except Exception:
                cls._trip_cooldown(cloud_url)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Retrieves team cache hit rates and token savings metrics."""
        is_team, lic_key = cls._is_team_tier()
        if not is_team:
            return {"active": False, "reason": "Requires active TokenSafe Team subscription"}

        endpoint = f"{cls._get_cloud_url()}/api/team/cache/stats"
        req = urllib.request.Request(
            endpoint,
            headers={"Authorization": f"Bearer {lic_key}", "User-Agent": "TokenSafe-TeamCache/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=max(2.5, cls.DEFAULT_TIMEOUT)) as resp:
                if resp.status == 200:
                    res = json.loads(resp.read().decode("utf-8"))
                    res["active"] = True
                    return res
        except Exception as e:
            return {"active": True, "error": str(e)}

        return {"active": True, "total_entries": 0, "total_hits": 0}
