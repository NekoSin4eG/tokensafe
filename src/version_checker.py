"""
TokenSafe Version Checker & Passive Nudge Engine.
Maintains a 24h cached version status to provide zero-latency terminal banners,
soft update recommendations, and critical forced update notifications.
"""

import os
import sys
import json
import time
import re
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

CURRENT_VERSION = "1.3.10"
CACHE_TTL = 86400  # 24 hours

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CACHE_FILE = os.path.join(DATA_DIR, "version_cache.json")
CLOUD_CHECK_URL = os.environ.get("TOKENSAFE_VERSION_URL", "https://api.tokensafe.dev/api/version/check")

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"

def semver_tuple(v_str: str) -> Tuple[int, ...]:
    """Parses semver string like '1.2.0' into numeric tuple (1, 2, 0)."""
    if not v_str:
        return (0, 0, 0)
    clean = re.sub(r"^[^\d]*", "", str(v_str).strip())
    parts = clean.split(".")
    nums = []
    for p in parts[:3]:
        m = re.match(r"\d+", p)
        nums.append(int(m.group(0)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

class VersionChecker:
    """Manages cached client-side version discovery and CLI banners."""

    @classmethod
    def get_cache_path(cls) -> str:
        os.makedirs(DATA_DIR, exist_ok=True)
        return CACHE_FILE

    @classmethod
    def load_cache(cls) -> Optional[Dict[str, Any]]:
        cache_path = cls.get_cache_path()
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @classmethod
    def save_cache(cls, data: Dict[str, Any]):
        cache_path = cls.get_cache_path()
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    @classmethod
    def check_version(
        cls,
        current_version: str = CURRENT_VERSION,
        license_key: str = "",
        force_fetch: bool = False,
        timeout: float = 1.5
    ) -> Dict[str, Any]:
        """
        Retrieves version status. Returns cached result if within TTL,
        otherwise makes a fast network request (< 1.5s timeout).
        Falls back gracefully on network or offline scenarios.
        """
        now = int(time.time())
        cached = cls.load_cache()

        if not force_fetch and cached:
            checked_at = cached.get("checked_at", 0)
            if now - checked_at < CACHE_TTL:
                return cached

        # Default payload
        payload = {
            "checked_at": now,
            "client_version": current_version,
            "latest_version": current_version,
            "min_supported_version": "1.1.0",
            "update_available": False,
            "force_update": False,
            "message": f"TokenSafe is up to date (v{current_version}).",
            "update_command": "tokensafe update"
        }

        try:
            params = f"?version={urllib.parse.quote(current_version)}&license={urllib.parse.quote(license_key)}"
            url = f"{CLOUD_CHECK_URL}{params}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"TokenSafe-CLI/{current_version}"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    data["checked_at"] = now
                    cls.save_cache(data)
                    return data
        except Exception:
            pass

        # Stale cache fallback if available
        if cached:
            return cached
        cls.save_cache(payload)
        return payload

    @classmethod
    def get_banner(cls, current_version: str = CURRENT_VERSION, license_key: str = "") -> Optional[str]:
        """
        Returns a styled ANSI banner string if an update is available or required,
        or None if up to date.
        """
        info = cls.check_version(current_version=current_version, license_key=license_key)
        force_update = info.get("force_update", False)
        update_available = info.get("update_available", False)
        latest = info.get("latest_version", current_version)
        cmd = info.get("update_command", "tokensafe update")

        if force_update:
            return (
                f"\n{RED}{BOLD}╔════════════════════════════════════════════════════════════════════════╗{RESET}\n"
                f"{RED}{BOLD}║ 🚨 CRITICAL UPDATE REQUIRED: TokenSafe v{current_version} is deprecated!          ║{RESET}\n"
                f"{RED}║ Please run {BOLD}{cmd}{RESET}{RED} immediately to continue using TokenSafe.   ║{RESET}\n"
                f"{RED}{BOLD}╚════════════════════════════════════════════════════════════════════════╝{RESET}\n"
            )
        elif update_available:
            return (
                f"\n{CYAN}💡 TokenSafe update available: {DIM}v{current_version}{RESET}{CYAN} → {GREEN}{BOLD}v{latest}{RESET}{CYAN}"
                f" (run {BOLD}{cmd}{RESET}{CYAN} to upgrade){RESET}\n"
            )

        return None
