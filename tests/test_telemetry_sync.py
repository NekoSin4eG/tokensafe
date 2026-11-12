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

    @patch("subprocess.Popen")
    @patch.object(TelemetrySync, "get_sync_status")
    def test_sync_background_reclaims_legacy_self_lock(self, mock_status, mock_popen):
        mock_status.return_value = {"pending_events": 3}
        TelemetrySync.save_state({"last_synced_at": 0})
        # Simulate the old self-deadlock where a long-lived parent (e.g. mcp_server.py)
        # wrote its own PID into .sync.lock.
        with open(ts_mod.SYNC_LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        TelemetrySync.sync_background(throttle_seconds=20.0)
        self.assertTrue(mock_popen.called)
        with open(ts_mod.SYNC_LOCK_PATH, "r", encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("spawn:"))

    def test_sync_background_real_worker_handoff(self):
        import http.server
        import threading
        import subprocess
        import sys

        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                received.extend(body.get("events", []))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"synced"}')

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()

        try:
            tracker = MetricsTracker(db_path=ts_mod.METRICS_DB_PATH)
            tracker.record_event("bg_s1", "PROMPT", 200, 50)
            tracker.record_event("bg_s1", "TOOL_READ", 400, 100)

            env = os.environ.copy()
            env["TOKENSAFE_DATA_DIR"] = self.tmp_dir.name
            env["TOKENSAFE_CLOUD_URL"] = f"http://127.0.0.1:{port}"
            env.pop("TOKENSAFE_TELEMETRY_DISABLED", None)

            claim_token = "testtoken1234"
            with open(ts_mod.SYNC_LOCK_PATH, "w", encoding="utf-8") as f:
                f.write(f"spawn:{claim_token}:{time.time():.3f}")

            proc = subprocess.run(
                [sys.executable, ts_mod.__file__, "--background", f"--claim-token={claim_token}"],
                env=env,
                timeout=15,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(received), 2)
            self.assertFalse(os.path.exists(ts_mod.SYNC_LOCK_PATH))
            st = TelemetrySync.get_state()
            self.assertEqual(st["last_synced_event_id"], 2)
        finally:
            server.shutdown()
            server.server_close()

    @patch("urllib.request.urlopen")
    def test_sync_drains_more_than_2000_events(self, mock_urlopen):
        tracker = MetricsTracker(db_path=ts_mod.METRICS_DB_PATH)
        for i in range(2050):
            tracker.record_event(f"s_{i}", "PROMPT", 100, 40)

        def make_resp(*args, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = json.dumps({"status": "synced"}).encode("utf-8")
            ctx = MagicMock()
            ctx.__enter__.return_value = resp
            return ctx

        mock_urlopen.side_effect = make_resp

        res = TelemetrySync.sync(
            cloud_url="http://mock-cloud.internal",
            license_key="TS1.mock.key",
            email="test@domain.com",
            batch_size=500,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["synced_events"], 2050)
        st = TelemetrySync.get_state()
        self.assertEqual(st["last_synced_event_id"], 2050)


if __name__ == "__main__":
    unittest.main()
