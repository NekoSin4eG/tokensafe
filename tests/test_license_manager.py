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
        from unittest.mock import MagicMock, patch
        mock_tracker = MagicMock()
        mock_tracker.get_monthly_saved_tokens.return_value = 150_000

        with patch.object(LicenseManager, "get_active_license", return_value=("free", {"tier": "free", "max_tokens_monthly": 500_000})):
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

    def test_freshest_valid_license_wins_across_data_and_global_files(self):
        import json
        import tempfile
        from unittest.mock import patch
        import src.license_manager as lm

        stale_key = LicenseManager.generate_license("solo", "user@example.com", duration_days=30, jti="old1")
        fresh_key = LicenseManager.generate_license("team", "user@example.com", duration_days=60, jti="new2")

        with tempfile.TemporaryDirectory() as tmpdir:
            data_lic = os.path.join(tmpdir, "data", "license.json")
            global_lic = os.path.join(tmpdir, "global", "license.json")
            os.makedirs(os.path.dirname(data_lic))
            os.makedirs(os.path.dirname(global_lic))
            with open(data_lic, "w", encoding="utf-8") as f:
                json.dump({"license_key": stale_key, "saved_at": 1000}, f)
            with open(global_lic, "w", encoding="utf-8") as f:
                json.dump({"license_key": fresh_key, "saved_at": 2000}, f)

            env_patch = {k: v for k, v in os.environ.items() if k not in ("TOKENSAFE_DATA_DIR", "TOKENSAFE_LICENSE_KEY")}
            with patch.dict(os.environ, env_patch, clear=True), \
                 patch.object(lm, "LICENSE_FILE", data_lic), \
                 patch.object(lm, "GLOBAL_LICENSE_FILE", global_lic):
                self.assertEqual(LicenseManager.get_active_license_key(), fresh_key)
                tier, payload = LicenseManager.get_active_license()
                self.assertEqual(tier, "team")
                self.assertEqual(payload.get("jti"), "new2")

    def test_expired_newer_file_falls_back_to_valid_license(self):
        import json
        import tempfile
        from unittest.mock import patch
        import src.license_manager as lm

        valid_key = LicenseManager.generate_license("team", "user@example.com", duration_days=30, jti="valid1")
        expired_key = LicenseManager.generate_license("solo", "user@example.com", duration_days=-1, jti="exp2")

        with tempfile.TemporaryDirectory() as tmpdir:
            data_lic = os.path.join(tmpdir, "data", "license.json")
            global_lic = os.path.join(tmpdir, "global", "license.json")
            os.makedirs(os.path.dirname(data_lic))
            os.makedirs(os.path.dirname(global_lic))
            with open(data_lic, "w", encoding="utf-8") as f:
                json.dump({"license_key": expired_key, "saved_at": 5000}, f)
            with open(global_lic, "w", encoding="utf-8") as f:
                json.dump({"license_key": valid_key, "saved_at": 1000}, f)

            env_patch = {k: v for k, v in os.environ.items() if k not in ("TOKENSAFE_DATA_DIR", "TOKENSAFE_LICENSE_KEY")}
            with patch.dict(os.environ, env_patch, clear=True), \
                 patch.object(lm, "LICENSE_FILE", data_lic), \
                 patch.object(lm, "GLOBAL_LICENSE_FILE", global_lic):
                self.assertEqual(LicenseManager.get_active_license_key(), valid_key)
                tier, _ = LicenseManager.get_active_license()
                self.assertEqual(tier, "team")

    def test_explicit_data_dir_isolated_from_global_license(self):
        import json
        import tempfile
        from unittest.mock import patch
        import src.license_manager as lm

        global_key = LicenseManager.generate_license("team", "user@example.com", duration_days=30)
        with tempfile.TemporaryDirectory() as tmpdir:
            isolated_dir = os.path.join(tmpdir, "isolated")
            os.makedirs(isolated_dir)
            global_lic = os.path.join(tmpdir, "global", "license.json")
            os.makedirs(os.path.dirname(global_lic))
            with open(global_lic, "w", encoding="utf-8") as f:
                json.dump({"license_key": global_key, "saved_at": 2000}, f)

            with patch.dict(os.environ, {"TOKENSAFE_DATA_DIR": isolated_dir}), \
                 patch.object(lm, "GLOBAL_LICENSE_FILE", global_lic):
                os.environ.pop("TOKENSAFE_LICENSE_KEY", None)
                self.assertEqual(LicenseManager.get_active_license_key(), "")
                tier, _ = LicenseManager.get_active_license()
                self.assertEqual(tier, "free")

if __name__ == "__main__":
    unittest.main()
