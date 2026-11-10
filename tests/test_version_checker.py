import unittest
import os
import json
import time
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


if __name__ == "__main__":
    unittest.main()
