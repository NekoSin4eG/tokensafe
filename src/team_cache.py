"""
TokenSafe Shared Team Cache Client.
Provides cross-developer prompt distillation and AST skeleton caching for Team tier.
Zero external runtime dependencies (standard library urllib + hashlib).
"""

import os
import json
import hashlib
import threading
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, Dict, Any, Tuple
from src.license_manager import LicenseManager

class TeamCache:
    """Distributed cross-developer cache client for TokenSafe Team tier."""

    DEFAULT_TIMEOUT = 0.4  # Strict 400ms timeout to prevent blocking agent turns

    @classmethod
    def _get_cloud_url(cls) -> str:
        return os.environ.get("TOKENSAFE_CLOUD_URL", "https://api.tokensafe.dev").rstrip("/")

    @classmethod
    def _is_team_tier(cls) -> Tuple[bool, str]:
        """Checks if current active license belongs to a Team tier subscription."""
        key = os.environ.get("TOKENSAFE_LICENSE_KEY", "").strip()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lic_file = os.path.join(base_dir, "data", "license.json")
        if not key and os.path.exists(lic_file):
            try:
                with open(lic_file, "r", encoding="utf-8") as f:
                    key = json.load(f).get("license_key", "").strip()
            except Exception:
                pass

        if not key:
            return False, ""

        valid, payload, _ = LicenseManager.validate_license(key)
        if valid and payload.get("tier") == "team":
            return True, key
        return False, ""

    @classmethod
    def hash_key(cls, key_text: str) -> str:
        """Returns lowercase SHA-256 hash of normalized text."""
        norm = " ".join((key_text or "").strip().split())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    @classmethod
    def get(cls, cache_type: str, key_text: str) -> Optional[str]:
        """
        Queries Shared Team Cache for compiled S-Expression or AST Skeleton.
        Returns cached value string or None if miss/disabled/timed out.
        """
        is_team, lic_key = cls._is_team_tier()
        if not is_team or not key_text:
            return None

        h = cls.hash_key(key_text)
        endpoint = f"{cls._get_cloud_url()}/api/team/cache?type={urllib.parse.quote(cache_type)}&hash={h}"

        req = urllib.request.Request(
            endpoint,
            headers={
                "Authorization": f"Bearer {lic_key}",
                "User-Agent": "TokenSafe-TeamCache/1.0"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=cls.DEFAULT_TIMEOUT) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("hit") and data.get("value"):
                        return data["value"]
        except Exception:
            # Graceful network degradation: fall back to local computation without error
            return None

        return None

    @classmethod
    def put(cls, cache_type: str, key_text: str, value: str, tokens_saved: int = 0) -> None:
        """
        Asynchronously broadcasts a freshly compiled distillation or skeleton to the Team Cache.
        Runs in a detached daemon thread so developer workflows never wait on network I/O.
        """
        is_team, lic_key = cls._is_team_tier()
        if not is_team or not key_text or not value:
            return

        h = cls.hash_key(key_text)

        def _worker():
            payload = {
                "type": cache_type,
                "hash": h,
                "value": value,
                "tokens_saved": max(0, tokens_saved),
                "license_key": lic_key
            }
            endpoint = f"{cls._get_cloud_url()}/api/team/cache"
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
                    pass
            except Exception:
                pass

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
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    res = json.loads(resp.read().decode("utf-8"))
                    res["active"] = True
                    return res
        except Exception as e:
            return {"active": True, "error": str(e)}

        return {"active": True, "total_entries": 0, "total_hits": 0}
