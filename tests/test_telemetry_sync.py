import os
import json
import time
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from src.telemetry_sync import TelemetrySync, SYNC_STATE_PATH
from src.metrics import MetricsTracker


class TestTelemetrySync(unittest.TestCase):
    def setUp(self):
        if os.path.exists(SYNC_STATE_PATH):
            try:
                os.remove(SYNC_STATE_PATH)
            except Exception:
                pass

    def tearDown(self):
        if os.path.exists(SYNC_STATE_PATH):
            try:
                os.remove(SYNC_STATE_PATH)
            except Exception:
                pass

    def test_get_and_save_state(self):
        st = TelemetrySync.get_state()
        self.assertEqual(st["last_synced_event_id"], 0)
        self.assertEqual(st["total_synced_events"], 0)

        TelemetrySync.save_state({
            "last_synced_event_id": 42,
            "last_synced_at": 1700000000,
            "total_synced_events": 100,
            "lifetime_rollup_synced": True
        })
        loaded = TelemetrySync.get_state()
        self.assertEqual(loaded["last_synced_event_id"], 42)
        self.assertEqual(loaded["total_synced_events"], 100)

    @patch("urllib.request.urlopen")
    def test_sync_success_flow(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"status": "synced", "records_saved": 5}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = TelemetrySync.sync(
            cloud_url="http://mock-cloud.internal",
            license_key="TS1.mock.key",
            email="test@domain.com"
        )
        self.assertTrue(res["success"])
        st = TelemetrySync.get_state()
        self.assertGreaterEqual(st["last_synced_at"], 0)

    @patch("subprocess.Popen")
    @patch.object(TelemetrySync, "get_sync_status")
    def test_sync_background_spawns_process_when_pending(self, mock_status, mock_popen):
        mock_status.return_value = {"pending_events": 5}
        TelemetrySync.save_state({"last_synced_at": 0})
        TelemetrySync.sync_background(throttle_seconds=60.0)
        self.assertTrue(mock_popen.called)

    @patch("subprocess.Popen")
    @patch.object(TelemetrySync, "get_sync_status")
    def test_sync_background_throttles(self, mock_status, mock_popen):
        mock_status.return_value = {"pending_events": 5}
        TelemetrySync.save_state({"last_synced_at": int(time.time())})
        TelemetrySync.sync_background(throttle_seconds=60.0)
        self.assertFalse(mock_popen.called)


if __name__ == "__main__":
    unittest.main()
