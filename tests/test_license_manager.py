import os
os.environ["TOKENSAFE_PRIVATE_KEY"] = "db13e3ddc4a9856417ad2236b2a4e96cf62b5a825dd044409b3f693bc77ff700"
os.environ["TOKENSAFE_PUBLIC_KEY"] = "8a2c9d550180ab5b00a06174cb6aecfd712740c59d0bdcff66a56515348c855e"

import unittest
import time
from src.license_manager import LicenseManager

class TestLicenseManager(unittest.TestCase):
    def test_generate_and_validate_solo_license(self):
        key = LicenseManager.generate_license("solo", "dev@example.com", duration_days=30)
        self.assertTrue(key.startswith(("TS1.", "TS2.")))
        valid, payload, msg = LicenseManager.validate_license(key)
        self.assertTrue(valid)
        self.assertEqual(payload["tier"], "solo")
        self.assertEqual(payload["email"], "dev@example.com")
        self.assertFalse(payload["trial"])

    def test_7_day_trial_license(self):
        key = LicenseManager.generate_license("solo", "trial@startup.io", duration_days=7, is_trial=True)
        valid, payload, msg = LicenseManager.validate_license(key)
        self.assertTrue(valid)
        self.assertTrue(payload["trial"])
        # Remaining time should be approximately 7 days (around 7 * 86400 seconds)
        remaining = payload["expires_at"] - int(time.time())
        self.assertGreater(remaining, 6 * 86400)
        self.assertLessEqual(remaining, 7 * 86400)

    def test_tampered_signature_rejected(self):
        key = LicenseManager.generate_license("solo", "dev@example.com", duration_days=30)
        parts = key.split(".")
        # Tamper payload or signature
        tampered_key = f"{parts[0]}.{parts[1]}.badsignature1234567890abcdef123"
        valid, payload, msg = LicenseManager.validate_license(tampered_key)
        self.assertFalse(valid)
        self.assertTrue("signature" in msg.lower() or "tampered" in msg.lower())

    def test_expired_license_rejected(self):
        # Generate with negative duration to simulate expiration
        key = LicenseManager.generate_license("solo", "expired@dev.org", duration_days=-1)
        valid, payload, msg = LicenseManager.validate_license(key)
        self.assertFalse(valid)
        self.assertIn("License expired", msg)

    def test_quota_free_tier(self):
        from unittest.mock import MagicMock
        mock_tracker = MagicMock()
        mock_tracker.get_monthly_saved_tokens.return_value = 150_000

        # Under 500k limit
        quota = LicenseManager.check_quota(mock_tracker)
        self.assertEqual(quota["tier"], "free")
        self.assertFalse(quota["is_quota_exceeded"])
        self.assertEqual(quota["monthly_limit"], 500_000)
        self.assertIsNone(quota["warning"])

        # Near 500k limit (>= 85%, e.g. 450k)
        mock_tracker.get_monthly_saved_tokens.return_value = 450_000
        quota_near = LicenseManager.check_quota(mock_tracker)
        self.assertFalse(quota_near["is_quota_exceeded"])
        self.assertIsNotNone(quota_near["warning"])
        self.assertIn("90%", quota_near["warning"])

        # Exceeded limit
        mock_tracker.get_monthly_saved_tokens.return_value = 520_000
        quota_exceeded = LicenseManager.check_quota(mock_tracker)
        self.assertTrue(quota_exceeded["is_quota_exceeded"])
        self.assertIsNotNone(quota_exceeded["warning"])
        self.assertIn("Quota Alert", quota_exceeded["warning"])

    def test_quota_solo_tier_unlimited(self):
        from unittest.mock import MagicMock, patch
        mock_tracker = MagicMock()
        mock_tracker.get_monthly_saved_tokens.return_value = 5_000_000
        with patch.object(LicenseManager, "get_active_license", return_value=("solo", {"tier": "solo"})):
            quota = LicenseManager.check_quota(mock_tracker)
            self.assertEqual(quota["tier"], "solo")
            self.assertFalse(quota["is_quota_exceeded"])
            self.assertIsNone(quota["monthly_limit"])
            self.assertIsNone(quota["warning"])

if __name__ == "__main__":
    unittest.main()
