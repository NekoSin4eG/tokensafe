import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.live_relay import LiveRelay, _machine_hash


class TestLiveRelay(unittest.TestCase):
    """Anonymized live relay: whitelisted/clamped events, throttled flush,
    and strict no-transmission without an active license."""

    def setUp(self):
        self._old_db = os.environ.get("TOKENSAFE_DB_PATH")
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "metrics.db")

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._old_db

    def test_record_clamps_and_whitelists(self):
        relay = LiveRelay()
        relay.record("cache_stats", {"tokens_saved": 10**12, "usd_saved": -5, "model": "x" * 300, "prompt": "SECRET"})
        with relay._lock:
            ev = list(relay._ring)[0]
        self.assertLessEqual(ev["saved_tokens"], 10_000_000)
        self.assertEqual(ev["usd_saved"], 0.0)
        self.assertLessEqual(len(ev["model"]), 40)
        self.assertNotIn("prompt", ev)
        self.assertNotIn("SECRET", json.dumps(ev))

    def test_machine_hash_stable_and_short(self):
        h1 = _machine_hash()
        h2 = _machine_hash()
        self.assertEqual(h1, h2)
        self.assertLessEqual(len(h1), 64)

    def test_flush_skips_without_license(self):
        from src.license_manager import LicenseManager
        relay = LiveRelay()
        relay.record("stats_update", {"tokens_saved": 42})
        opened = []
        with patch.object(LicenseManager, "get_active_license_key", return_value=""), \
             patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: opened.append(req)):
            relay._flush()
        self.assertEqual(opened, [], "license-less install must never transmit")

    def test_flush_posts_batch_with_license(self):
        from src.license_manager import LicenseManager
        relay = LiveRelay()
        relay.record("stats_update", {"tokens_saved": 42, "usd_saved": 0.1, "model": "claude"})
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        with patch.object(LicenseManager, "get_active_license_key", return_value="TS2.testkey"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            relay._flush()
        self.assertIn("/api/live/relay", captured["url"])
        self.assertEqual(captured["body"]["license_key"], "TS2.testkey")
        self.assertEqual(captured["body"]["events"][0]["saved_tokens"], 42)


if __name__ == "__main__":
    unittest.main()
