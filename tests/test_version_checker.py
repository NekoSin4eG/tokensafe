import unittest
import os
import json
import time
from src.version_checker import VersionChecker, semver_tuple

class TestVersionChecker(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
