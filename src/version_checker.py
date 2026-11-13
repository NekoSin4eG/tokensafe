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
import shlex
import subprocess
import threading
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

CURRENT_VERSION = "3.0.0"
CACHE_TTL = 86400  # 24 hours
# When the cloud is unreachable we must NOT cache the synthesized "up to date"
# answer for a full day — otherwise an update released right after an outage
# stays invisible for up to 24h (this is exactly why the macOS dialog appeared
# "broken" while the backend was redeploying).
FALLBACK_CACHE_TTL = 900  # 15 minutes

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Script-mode guard: notify_os_update_background spawns this file directly
# (`python3 src/version_checker.py --check-bg`), which sets sys.path[0] to the
# src/ directory — `import src.*` fails unless PROJECT_ROOT is on the path
# FIRST. The old ordering (import before path setup) made every background
# update check die silently in the bare except of __main__, so version_cache.json
# was never written and the update dialog never appeared.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.paths import resolve_data_dir

DATA_DIR = resolve_data_dir()
CACHE_FILE = os.path.join(DATA_DIR, "version_cache.json")
_DEFAULT_DATA_DIR = DATA_DIR
_DEFAULT_CACHE_FILE = CACHE_FILE
CLOUD_CHECK_URL = os.environ.get("TOKENSAFE_VERSION_URL", "https://api.tokensafe.dev/api/version/check")

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"

class VersionChecker:
    """Manages cached client-side version discovery and CLI banners."""

    @classmethod
    def _effective_data_dir(cls) -> str:
        if DATA_DIR != _DEFAULT_DATA_DIR:
            return DATA_DIR
        return resolve_data_dir()

    @classmethod
    def get_cache_path(cls) -> str:
        if CACHE_FILE != _DEFAULT_CACHE_FILE:
            parent = os.path.dirname(CACHE_FILE) or "."
            os.makedirs(parent, exist_ok=True)
            return CACHE_FILE
        dd = cls._effective_data_dir()
        os.makedirs(dd, exist_ok=True)
        return os.path.join(dd, "version_cache.json")

    @classmethod
    def load_cache(cls) -> Optional[Dict[str, Any]]:
        cache_path = cls.get_cache_path()
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @classmethod
    def save_cache(cls, data: Dict[str, Any]):
        cache_path = cls.get_cache_path()
        try:
            tmp = f"{cache_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, cache_path)
        except Exception:
            pass

    @staticmethod
    def _cache_matches_version(cached: Optional[Dict[str, Any]], current_version: str) -> bool:
        """True when cached payload was recorded for `current_version` (invalidates cache on upgrade)."""
        if not cached:
            return False
        cached_ver = str(cached.get("client_version") or "").strip().lstrip("v")
        curr_ver = str(current_version or "").strip().lstrip("v")
        return bool(cached_ver) and cached_ver == curr_ver

    @classmethod
    def check_version(
        cls,
        current_version: str = CURRENT_VERSION,
        license_key: str = "",
        force_fetch: bool = False,
        timeout: float = 1.5
    ) -> Dict[str, Any]:
        """
        Retrieves version status. Returns cached result if within TTL and matching
        `current_version`, otherwise makes a fast network request (< 1.5s timeout).
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
        cache_matches = cls._cache_matches_version(cached, current_version)

        if not force_fetch and cache_matches:
            checked_at = cached.get("checked_at", 0)
            ttl = int(cached.get("ttl", CACHE_TTL))
            if now - checked_at < ttl:
                return cached

        # Default payload (offline fallback — cached only briefly, see FALLBACK_CACHE_TTL)
        payload = {
            "checked_at": now,
            "ttl": FALLBACK_CACHE_TTL,
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
                    # TS1 sunset: transparently swap in the reissued TS2 key.
                    # Never persist the key inside version_cache.json — it is a secret.
                    reissued = data.pop("reissued_license_key", "")
                    if reissued and reissued.startswith("TS2."):
                        try:
                            from src.license_manager import LicenseManager
                            LicenseManager.save_license(reissued)
                        except Exception:
                            pass
                    data.pop("ttl", None)  # authoritative server answer: full 24h cache
                    cls.save_cache(data)
                    return data
        except Exception:
            pass

        # Stale cache fallback if available for the same installed version
        if cache_matches:
            return cached
        cls.save_cache(payload)
        return payload

    @classmethod
    def get_banner(
        cls,
        current_version: str = CURRENT_VERSION,
        license_key: str = "",
        cache_only: bool = False,
    ) -> Optional[str]:
        """
        Returns a styled ANSI banner string if an update is available or required,
        or None if up to date. When cache_only=True, reads cached version state
        without blocking on network roundtrips.
        """
        if cache_only:
            cached = cls.load_cache()
            if not cls._cache_matches_version(cached, current_version):
                return None
            info = cached or {}
        else:
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

    @staticmethod
    def _posix_single_quote(text: str) -> str:
        """Quotes text for safe embedding inside a single-quoted shell string."""
        return "'" + text.replace("'", "'\\''") + "'"

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
                # TCC-safe: driving Terminal.app via AppleScript requires an Automation
                # permission prompt that background (hook-spawned) processes can never
                # satisfy, which made "Update Now" silently fail. `do shell script` needs
                # no such permission. The updater runs inside a LOGIN shell so that
                # profile-managed PATH entries (nvm, homebrew, volta, ~/.local/bin…)
                # resolve the `tokensafe` executable; a bare non-interactive shell
                # would not see them and log "command not found".
                inner_cmd = f"nohup {update_command} > /tmp/tokensafe-update.log 2>&1 &"
                shell_cmd = (
                    'SHELL_BIN=${SHELL:-/bin/zsh}; [ -x "$SHELL_BIN" ] || SHELL_BIN=/bin/zsh; '
                    f'nohup "$SHELL_BIN" -lc {cls._posix_single_quote(inner_cmd)} >/dev/null 2>&1 &'
                )
                # Escape the shell command for embedding inside an AppleScript string literal
                escaped_shell = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
                apple_script = f'''
                set dialogResult to display dialog "A new version of TokenSafe (v{latest_version}) is available.\\n\\nCurrent version: v{current_version}\\nLatest version: v{latest_version}\\n\\nWould you like to update now?" with title "TokenSafe Update Available" buttons {{"Later", "Update Now"}} default button "Update Now" with icon note
                if button returned of dialogResult is "Update Now" then
                    do shell script "{escaped_shell}"
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
                        cmd_argv = shlex.split(update_command) or [update_command]
                        for term in (
                            ["x-terminal-emulator", "-e", *cmd_argv],
                            ["gnome-terminal", "--", *cmd_argv],
                            ["konsole", "-e", *cmd_argv],
                            ["xterm", "-e", *cmd_argv],
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

        # Cross-process claim BEFORE checking/updating last_os_notify_at so two concurrent
        # processes never both pass the 24h guard and show duplicate dialogs.
        dd = cls._effective_data_dir()
        os.makedirs(dd, exist_ok=True)
        claim_path = os.path.join(dd, ".update_dialog.claim")
        now = int(time.time())
        try:
            if os.path.exists(claim_path):
                if (now - os.path.getmtime(claim_path)) < 60:
                    return False
                os.remove(claim_path)
            fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
        except FileExistsError:
            return False
        except OSError:
            pass

        try:
            # Re-read cache inside the lock/claim so stale or concurrent updates are visible
            cached = cls.load_cache() or dict(info)
            last_notified_ver = cached.get("last_os_notify_version", "")
            last_notified_at = cached.get("last_os_notify_at", 0)
            if not force and last_notified_ver == latest and (now - last_notified_at) < CACHE_TTL:
                return False

            dispatched = cls.show_os_dialog(
                current_version=current_version,
                latest_version=latest,
                update_command=cmd
            )
            if dispatched:
                try:
                    cached["last_os_notify_version"] = latest
                    cached["last_os_notify_at"] = now
                    cls.save_cache(cached)
                except Exception:
                    pass
            return dispatched
        finally:
            try:
                os.remove(claim_path)
            except OSError:
                pass

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
        if not force and cls._cache_matches_version(cached, current_version):
            checked_at = cached.get("checked_at", 0)
            update_available = cached.get("update_available", False)
            latest = cached.get("latest_version", current_version)
            last_notified_ver = cached.get("last_os_notify_version", "")
            last_notified_at = cached.get("last_os_notify_at", 0)

            # If cache is valid and no update is available, skip completely
            # (ttl-aware: offline fallback entries expire after FALLBACK_CACHE_TTL)
            ttl = int(cached.get("ttl", CACHE_TTL))
            if now - checked_at < ttl and not update_available:
                return

            # If update is available but user was already notified within 24h, skip
            if update_available and last_notified_ver == latest and (now - last_notified_at < CACHE_TTL):
                return

        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            script_path = os.path.abspath(__file__)
            cmd = [sys.executable, script_path, "--check-bg", f"--current-version={current_version}"]
            if force:
                cmd.append("--force")
            child_env = dict(os.environ)
            if license_key:
                child_env["TOKENSAFE_BG_LICENSE_KEY"] = license_key
            subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                env=child_env,
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
            bg_ver = CURRENT_VERSION
            for a in sys.argv[1:]:
                if a.startswith("--current-version="):
                    bg_ver = a.split("=", 1)[1].strip() or CURRENT_VERSION
            bg_key = os.environ.get("TOKENSAFE_BG_LICENSE_KEY", "")
            VersionChecker.notify_os_update(current_version=bg_ver, license_key=bg_key, force=is_force)
        except Exception:
            pass
