import unittest
import time
from src.license_manager import LicenseManager

class TestLicenseManager(unittest.TestCase):
    def test_generate_and_validate_solo_license(self):
        key = LicenseManager.generate_license("solo", "dev@example.com", duration_days=30)
        self.assertTrue(key.startswith("TS1."))
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
        self.assertIn("Invalid signature", msg)

    def test_expired_license_rejected(self):
        # Generate with negative duration to simulate expiration
        key = LicenseManager.generate_license("solo", "expired@dev.org", duration_days=-1)
        valid, payload, msg = LicenseManager.validate_license(key)
        self.assertFalse(valid)
        self.assertIn("License expired", msg)

if __name__ == "__main__":
    unittest.main()
