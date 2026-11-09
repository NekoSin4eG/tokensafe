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
import subprocess
import threading
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

CURRENT_VERSION = "1.4.7"
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
        if not license_key:
            try:
                from src.license_manager import LicenseManager
                license_key = LicenseManager.get_active_license_key()
            except Exception:
                license_key = ""

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
            import platform as _plat
            plat_str = f"{_plat.system().lower()}-{_plat.machine().lower()}"
            params = f"?version={urllib.parse.quote(current_version)}&license={urllib.parse.quote(license_key)}&platform={urllib.parse.quote(plat_str)}&client_type=cli"
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

    @classmethod
    def show_os_dialog(
        cls,
        current_version: str = CURRENT_VERSION,
        latest_version: str = CURRENT_VERSION,
        update_command: str = "tokensafe update"
    ) -> bool:
        """
        Displays a native OS desktop popup dialog with English texts and 'Update Now' / 'Later' buttons.
        Uses zero external third-party packages (macOS AppleScript, Windows ctypes/PowerShell, Linux zenity/notify-send).
        """
        plat = sys.platform

        if plat == "darwin":
            try:
                escaped_cmd = update_command.replace('"', '\\"')
                apple_script = f'''
                set dialogResult to display dialog "A new version of TokenSafe (v{latest_version}) is available.\\n\\nCurrent version: v{current_version}\\nLatest version: v{latest_version}\\n\\nWould you like to update now?" with title "TokenSafe Update Available" buttons {{"Later", "Update Now"}} default button "Update Now" with icon note
                if button returned of dialogResult is "Update Now" then
                    tell application "Terminal"
                        activate
                        do script "{escaped_cmd}"
                    end tell
                end if
                '''
                subprocess.Popen(
                    ["osascript", "-e", apple_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
                return True
            except Exception:
                return False

        elif plat == "win32":
            try:
                import ctypes
                flags = 0x00000004 | 0x00000040 | 0x00010000 | 0x00040000  # MB_YESNO | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
                msg = (
                    f"A new version of TokenSafe (v{latest_version}) is available.\n\n"
                    f"Current version: v{current_version}\n"
                    f"Latest version:  v{latest_version}\n\n"
                    "Would you like to update now?"
                )
                title = "TokenSafe Update Available"

                def _win_worker():
                    res = ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
                    if res == 6:  # IDYES
                        subprocess.Popen(
                            ["cmd.exe", "/c", "start", "cmd.exe", "/k", update_command],
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                        )

                threading.Thread(target=_win_worker, daemon=True).start()
                return True
            except Exception:
                return False

        elif plat.startswith("linux"):
            try:
                zenity_cmd = [
                    "zenity",
                    "--question",
                    "--title=TokenSafe Update Available",
                    f"--text=A new version of TokenSafe (v{latest_version}) is available.\n\nCurrent version: v{current_version}\nLatest version: v{latest_version}\n\nWould you like to update now?",
                    "--ok-label=Update Now",
                    "--cancel-label=Later"
                ]
                p = subprocess.Popen(zenity_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                def _zenity_wait():
                    if p.wait() == 0:
                        for term in (
                            ["x-terminal-emulator", "-e", update_command],
                            ["gnome-terminal", "--", update_command],
                            ["konsole", "-e", update_command],
                            ["xterm", "-e", update_command]
                        ):
                            try:
                                subprocess.Popen(term)
                                break
                            except Exception:
                                pass

                threading.Thread(target=_zenity_wait, daemon=True).start()
                return True
            except Exception:
                pass

            try:
                subprocess.Popen([
                    "notify-send",
                    "TokenSafe Update Available",
                    f"A new version (v{latest_version}) is available. Run: {update_command}"
                ])
                return True
            except Exception:
                return False

        return False

    @classmethod
    def notify_os_update(
        cls,
        current_version: str = CURRENT_VERSION,
        license_key: str = "",
        force: bool = False,
        timeout: float = 1.5
    ) -> bool:
        """
        Checks if an update is available and presents a native OS desktop popup dialog.
        Throttled to at most once per 24 hours per target version unless force=True.
        Returns True if prompt was dispatched, False otherwise.
        """
        info = cls.check_version(current_version=current_version, license_key=license_key, timeout=timeout)
        update_available = info.get("update_available", False)
        latest = info.get("latest_version", current_version)
        cmd = info.get("update_command", "tokensafe update")

        if not update_available and not force:
            return False

        cached = cls.load_cache() or {}
        now = int(time.time())
        last_notified_ver = cached.get("last_os_notify_version", "")
        last_notified_at = cached.get("last_os_notify_at", 0)

        if not force and last_notified_ver == latest and (now - last_notified_at) < CACHE_TTL:
            return False

        cached["last_os_notify_version"] = latest
        cached["last_os_notify_at"] = now
        cls.save_cache(cached)

        return cls.show_os_dialog(
            current_version=current_version,
            latest_version=latest,
            update_command=cmd
        )

    @classmethod
    def notify_os_update_background(
        cls,
        current_version: str = CURRENT_VERSION,
        license_key: str = "",
        force: bool = False
    ):
        """
        Dispatches notify_os_update in a detached, non-blocking background OS process.
        Avoids in-process daemon threads to prevent OpenSSL cleanup SIGSEGV races during fast CLI exit.
        """
        now = int(time.time())
        cached = cls.load_cache()
        if not force and cached:
            checked_at = cached.get("checked_at", 0)
            update_available = cached.get("update_available", False)
            latest = cached.get("latest_version", current_version)
            last_notified_ver = cached.get("last_os_notify_version", "")
            last_notified_at = cached.get("last_os_notify_at", 0)

            # If cache is valid and no update is available, skip completely
            if now - checked_at < CACHE_TTL and not update_available:
                return

            # If update is available but user was already notified within 24h, skip
            if update_available and last_notified_ver == latest and (now - last_notified_at < CACHE_TTL):
                return

        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            script_path = os.path.abspath(__file__)
            cmd = [sys.executable, script_path, "--check-bg"]
            if force:
                cmd.append("--force")
            subprocess.Popen(
                cmd,
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
    if "--check-bg" in sys.argv:
        try:
            is_force = "--force" in sys.argv
            VersionChecker.notify_os_update(force=is_force)
        except Exception:
            pass
