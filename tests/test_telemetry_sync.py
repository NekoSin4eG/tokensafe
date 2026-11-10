import os
import json
import time
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import src.telemetry_sync as ts_mod
from src.telemetry_sync import TelemetrySync
from src.metrics import MetricsTracker


class TestTelemetrySync(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_data_dir = ts_mod.DATA_DIR
        self.orig_state_path = ts_mod.SYNC_STATE_PATH
        self.orig_lock_path = ts_mod.SYNC_LOCK_PATH
        self.orig_db_path = ts_mod.METRICS_DB_PATH

        ts_mod.DATA_DIR = self.tmp_dir.name
        ts_mod.SYNC_STATE_PATH = os.path.join(self.tmp_dir.name, "sync_state.json")
        ts_mod.SYNC_LOCK_PATH = os.path.join(self.tmp_dir.name, ".sync.lock")
        ts_mod.METRICS_DB_PATH = os.path.join(self.tmp_dir.name, "metrics.db")

    def tearDown(self):
        ts_mod.DATA_DIR = self.orig_data_dir
        ts_mod.SYNC_STATE_PATH = self.orig_state_path
        ts_mod.SYNC_LOCK_PATH = self.orig_lock_path
        ts_mod.METRICS_DB_PATH = self.orig_db_path
        self.tmp_dir.cleanup()

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
        tracker = MetricsTracker(db_path=ts_mod.METRICS_DB_PATH)
        tracker.record_event("s1", "PROMPT", 200, 50)
        tracker.record_event("s1", "TOOL_READ", 400, 100)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"status": "synced", "records_saved": 2}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = TelemetrySync.sync(
            cloud_url="http://mock-cloud.internal",
            license_key="TS1.mock.key",
            email="test@domain.com"
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["synced_events"], 2)
        st = TelemetrySync.get_state()
        self.assertEqual(st["last_synced_event_id"], 2)
        self.assertGreater(st["last_synced_at"], 0)

    @patch("urllib.request.urlopen")
    def test_sync_checkpoints_each_batch_on_partial_failure(self, mock_urlopen):
        tracker = MetricsTracker(db_path=ts_mod.METRICS_DB_PATH)
        for i in range(5):
            tracker.record_event(f"s_{i}", "PROMPT", 100, 40)

        ok_resp = MagicMock()
        ok_resp.status = 200
        ok_resp.read.return_value = json.dumps({"status": "synced"}).encode("utf-8")
        ok_ctx = MagicMock()
        ok_ctx.__enter__.return_value = ok_resp

        # Batch 1 (2 events) succeeds; batch 2 (2 events) times out
        mock_urlopen.side_effect = [ok_ctx, urllib.error.URLError("timed out")]

        res = TelemetrySync.sync(
            cloud_url="http://mock-cloud.internal",
            license_key="TS1.mock.key",
            email="test@domain.com",
            batch_size=2
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["synced_events"], 2)
        st = TelemetrySync.get_state()
        # Cursor MUST advance to batch 1 max ID so those events are never replayed
        self.assertEqual(st["last_synced_event_id"], 2)
        self.assertEqual(st["total_synced_events"], 2)

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
