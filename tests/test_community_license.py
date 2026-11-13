"""
Unit tests for the zero-config Community Edition license flow:
- LicenseManager.ensure_community_license (consent switch, skip rules, cooldown, fail-open)
- generate_license(quota_monthly=...) signed payload embedding
- check_quota server-issued quota override
"""

import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from src.license_manager import LicenseManager


def _fake_response(payload: dict, status: int = 200):
    class _Resp(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp(json.dumps(payload).encode("utf-8"))


class TestEnsureCommunityLicense(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TOKENSAFE_TELEMETRY_DISABLED", None)
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch("src.license_manager.resolve_data_dir", return_value=self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        os.environ.pop("TOKENSAFE_TELEMETRY_DISABLED", None)

    def test_skips_when_telemetry_disabled(self):
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        with mock.patch("urllib.request.urlopen") as m_urlopen:
            res = LicenseManager.ensure_community_license()
        self.assertEqual(res, {"success": False, "reason": "telemetry_disabled"})
        m_urlopen.assert_not_called()

    def test_skips_when_license_present(self):
        with mock.patch.object(LicenseManager, "get_active_license_key", return_value="TS2.abc.def"), \
             mock.patch("urllib.request.urlopen") as m_urlopen:
            res = LicenseManager.ensure_community_license()
        self.assertEqual(res, {"success": False, "reason": "license_present"})
        m_urlopen.assert_not_called()

    def test_issued_license_saved_and_cooldown_applies(self):
        with mock.patch.object(LicenseManager, "get_active_license_key", return_value=""), \
             mock.patch.object(LicenseManager, "save_license", return_value=(True, "ok")) as m_save, \
             mock.patch("urllib.request.urlopen", return_value=_fake_response({"success": True, "license_key": "TS2.xyz.sig"})) as m_urlopen:
            res = LicenseManager.ensure_community_license()
        self.assertEqual(res, {"success": True, "reason": "issued"})
        m_save.assert_called_once_with("TS2.xyz.sig")
        # Fingerprint must be a 32-char hex string in the request body
        body = json.loads(m_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertRegex(body["machine_fp"], r"^[0-9a-f]{32}$")

        # Immediate second call hits the 24h cooldown — no network
        with mock.patch.object(LicenseManager, "get_active_license_key", return_value=""), \
             mock.patch("urllib.request.urlopen") as m_urlopen2:
            res2 = LicenseManager.ensure_community_license()
        self.assertEqual(res2, {"success": False, "reason": "cooldown"})
        m_urlopen2.assert_not_called()

    def test_network_failure_is_fail_open(self):
        with mock.patch.object(LicenseManager, "get_active_license_key", return_value=""), \
             mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
            res = LicenseManager.ensure_community_license()
        self.assertEqual(res, {"success": False, "reason": "error"})

    def test_server_rejection_is_fail_open(self):
        with mock.patch.object(LicenseManager, "get_active_license_key", return_value=""), \
             mock.patch("urllib.request.urlopen", return_value=_fake_response({"success": False, "error": "denied"}, status=403)):
            res = LicenseManager.ensure_community_license()
        self.assertEqual(res, {"success": False, "reason": "error"})


class TestQuotaOverride(unittest.TestCase):
    def test_server_quota_overrides_embedded_default(self):
        tracker = mock.Mock()
        tracker.get_monthly_saved_tokens.return_value = 150
        with mock.patch.object(
            LicenseManager,
            "get_active_license",
            return_value=("free", {"tier": "free", "max_tokens_monthly": 100}),
        ):
            res = LicenseManager.check_quota(tracker)
        self.assertEqual(res["monthly_limit"], 100)
        self.assertTrue(res["is_quota_exceeded"])

    def test_embedded_default_500k_when_payload_carries_no_server_quota(self):
        tracker = mock.Mock()
        tracker.get_monthly_saved_tokens.return_value = 499_999
        with mock.patch.object(
            LicenseManager,
            "get_active_license",
            return_value=("free", {"tier": "free"}),
        ):
            res = LicenseManager.check_quota(tracker)
        self.assertEqual(res["monthly_limit"], 500_000)
        self.assertFalse(res["is_quota_exceeded"])

    def test_paid_tier_stays_unlimited(self):
        tracker = mock.Mock()
        with mock.patch.object(LicenseManager, "get_active_license", return_value=("solo", {"tier": "solo"})):
            res = LicenseManager.check_quota(tracker)
        self.assertIsNone(res["monthly_limit"])
        self.assertFalse(res["is_quota_exceeded"])


class TestGenerateLicenseQuotaEmbedding(unittest.TestCase):
    def test_quota_monthly_embedded_in_signed_payload(self):
        priv_hex = "ab" * 32
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            pub_hex = (
                Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
                .public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
                .hex()
            )
        except Exception:
            self.skipTest("cryptography backend unavailable")

        with mock.patch("src.license_manager.DEFAULT_ED25519_PUBLIC_KEY", pub_hex):
            key = LicenseManager.generate_license(
                tier="free",
                email="community@tokensafe.dev",
                duration_days=30,
                jti="testjti",
                quota_monthly=12345,
                signing_key=priv_hex,
            )
            ok, payload, msg = LicenseManager.validate_license(key)
        self.assertTrue(ok, msg)
        self.assertEqual(payload.get("tier"), "free")
        self.assertEqual(payload.get("max_tokens_monthly"), 12345)
        self.assertEqual(payload.get("jti"), "testjti")

    def test_quota_omitted_when_not_provided(self):
        priv_hex = "cd" * 32
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            pub_hex = (
                Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
                .public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
                .hex()
            )
        except Exception:
            self.skipTest("cryptography backend unavailable")

        with mock.patch("src.license_manager.DEFAULT_ED25519_PUBLIC_KEY", pub_hex):
            key = LicenseManager.generate_license(
                tier="solo",
                email="dev@test.dev",
                duration_days=30,
                signing_key=priv_hex,
            )
            ok, payload, msg = LicenseManager.validate_license(key)
        self.assertTrue(ok, msg)
        self.assertNotIn("max_tokens_monthly", payload)


if __name__ == "__main__":
    unittest.main()
