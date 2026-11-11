import unittest
import os
import sys
import json
import time
import subprocess
import tempfile
import shutil
from src.version_checker import VersionChecker, semver_tuple

class TestVersionChecker(unittest.TestCase):
    def setUp(self):
        from src.version_checker import CACHE_FILE
        self.cache_backup = None
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    self.cache_backup = f.read()
            except Exception:
                pass

    def tearDown(self):
        from src.version_checker import CACHE_FILE
        if self.cache_backup is not None:
            try:
                with open(CACHE_FILE, "w") as f:
                    f.write(self.cache_backup)
            except Exception:
                pass
        elif os.path.exists(CACHE_FILE):
            try:
                os.remove(CACHE_FILE)
            except Exception:
                pass

    def test_semver_tuple_parsing(self):
        self.assertEqual(semver_tuple("1.2.0"), (1, 2, 0))
        self.assertEqual(semver_tuple("v1.2.0"), (1, 2, 0))
        self.assertEqual(semver_tuple("1.10.5"), (1, 10, 5))
        self.assertEqual(semver_tuple("2.0"), (2, 0, 0))
        self.assertEqual(semver_tuple(""), (0, 0, 0))
        self.assertTrue(semver_tuple("1.2.0") > semver_tuple("1.1.9"))
        self.assertTrue(semver_tuple("1.10.0") > semver_tuple("1.2.0"))

    def test_cache_save_and_load(self):
        data = {
            "checked_at": int(time.time()),
            "client_version": "1.2.0",
            "latest_version": "1.2.0",
            "update_available": False,
            "force_update": False
        }
        VersionChecker.save_cache(data)
        loaded = VersionChecker.load_cache()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["client_version"], "1.2.0")

    def test_banner_rendering_soft_nudge(self):
        # Mock cached data with update available
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "1.1.0",
            "latest_version": "1.2.0",
            "min_supported_version": "1.0.0",
            "update_available": True,
            "force_update": False,
            "update_command": "tokensafe update"
        }
        VersionChecker.save_cache(mock_data)
        banner = VersionChecker.get_banner(current_version="1.1.0")
        self.assertIsNotNone(banner)
        self.assertIn("tokensafe update", banner)
        self.assertIn("1.2.0", banner)

    def test_banner_rendering_critical_forced_update(self):
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "0.9.0",
            "latest_version": "1.2.0",
            "min_supported_version": "1.1.0",
            "update_available": True,
            "force_update": True,
            "update_command": "tokensafe update"
        }
        VersionChecker.save_cache(mock_data)
        banner = VersionChecker.get_banner(current_version="0.9.0")
        self.assertIsNotNone(banner)
        self.assertIn("CRITICAL UPDATE REQUIRED", banner)
        self.assertIn("deprecated", banner)

    def test_banner_rendering_up_to_date(self):
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "1.2.0",
            "latest_version": "1.2.0",
            "min_supported_version": "1.1.0",
            "update_available": False,
            "force_update": False
        }
        VersionChecker.save_cache(mock_data)
        banner = VersionChecker.get_banner(current_version="1.2.0")
        self.assertIsNone(banner)

    def test_notify_os_update_throttled(self):
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "1.2.0",
            "latest_version": "1.3.0",
            "update_available": True,
            "last_os_notify_at": int(time.time()),
            "last_os_notify_version": "1.3.0"
        }
        VersionChecker.save_cache(mock_data)
        # Should not notify because last_os_notify_at is current
        res = VersionChecker.notify_os_update(current_version="1.2.0", force=False)
        self.assertFalse(res)

    def test_notify_os_update_force(self):
        from unittest.mock import patch
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "1.2.0",
            "latest_version": "1.3.0",
            "update_available": True
        }
        VersionChecker.save_cache(mock_data)
        with patch.object(VersionChecker, "show_os_dialog", return_value=True) as mock_dialog:
            res = VersionChecker.notify_os_update(current_version="1.2.0", force=True)
            self.assertTrue(res)
            self.assertTrue(mock_dialog.called)

    def test_notify_os_update_background_spawns_process(self):
        from unittest.mock import patch
        mock_data = {
            "checked_at": int(time.time()) - 100000,
            "client_version": "1.2.0",
            "latest_version": "1.3.0",
            "update_available": True
        }
        VersionChecker.save_cache(mock_data)
        with patch("subprocess.Popen") as mock_popen:
            VersionChecker.notify_os_update_background(current_version="1.2.0", force=True)
            self.assertTrue(mock_popen.called)
            args, kwargs = mock_popen.call_args
            self.assertIn("--check-bg", args[0])
            self.assertIn("--force", args[0])

    def test_check_bg_script_mode_imports_and_caches(self):
        """Regression: `python3 src/version_checker.py --check-bg` used to die
        on `from src.paths import ...` (sys.path[0] = src/, no PROJECT_ROOT on
        the path) and the bare except swallowed it — version_cache.json was
        never written. The subprocess must now exit cleanly and create the
        cache (fallback payload via an unroutable version URL)."""
        tmp_data = tempfile.mkdtemp(prefix="ts_vcheck_")
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = dict(os.environ)
            env["TOKENSAFE_DATA_DIR"] = tmp_data
            env["TOKENSAFE_VERSION_URL"] = "http://127.0.0.1:9/api/version/check"
            proc = subprocess.run(
                [sys.executable, os.path.join(project_root, "src", "version_checker.py"), "--check-bg"],
                cwd=project_root, env=env, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            cache_path = os.path.join(tmp_data, "version_cache.json")
            self.assertTrue(os.path.exists(cache_path), "background check must persist the version cache")
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("client_version", data)
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)

    def test_notify_marks_cache_only_after_dialog_dispatch(self):
        """last_os_notify_version must be written only when the dialog actually
        dispatched — a silently TCC-blocked osascript must not buy 24h of silence."""
        from unittest.mock import patch
        mock_data = {
            "checked_at": int(time.time()),
            "client_version": "1.2.0",
            "latest_version": "1.3.0",
            "update_available": True
        }
        VersionChecker.save_cache(mock_data)
        with patch.object(VersionChecker, "show_os_dialog", return_value=False):
            res = VersionChecker.notify_os_update(current_version="1.2.0", force=True)
            self.assertFalse(res)
            cached = VersionChecker.load_cache() or {}
            self.assertNotIn("last_os_notify_version", cached)
        with patch.object(VersionChecker, "show_os_dialog", return_value=True):
            res = VersionChecker.notify_os_update(current_version="1.2.0", force=True)
            self.assertTrue(res)
            cached = VersionChecker.load_cache() or {}
            self.assertEqual(cached.get("last_os_notify_version"), "1.3.0")


if __name__ == "__main__":
    unittest.main()
