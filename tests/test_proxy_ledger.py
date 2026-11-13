"""
Integration tests: Tool Result Ledger + Tool Pruning stages inside the proxy
pipeline (v3.0.0). Harness mirrors tests/test_epoch_governor.py.
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.result_ledger import FOLD_MARKER


class TestProxyLedgerIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_pxlg_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        for flag in ("TOKENSAFE_TOOL_LEDGER", "TOKENSAFE_TOOL_PRUNING",
                     "TOKENSAFE_TOOL_PRUNE_KEEP", "TOKENSAFE_TOOL_PRUNE_ALLOWLIST"):
            os.environ.pop(flag, None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TOOL_PRUNING", None)
        self._state_patch = patch("src.tool_pruner._state_path",
                                  return_value=os.path.join(self._tmp, "state.json"))
        self._state_patch.start()
        self.addCleanup(self._state_patch.stop)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()
        # Keep archives inside the tmp sandbox, never ~/.tokensafe
        self._data_patch = patch("src.paths.resolve_data_dir", return_value=self._tmp)
        self._data_patch.start()
        self.addCleanup(self._data_patch.stop)

    def _invoke(self, payload_obj):
        from src.proxy import TokenSafeProxyHandler
        captured = []
        body = json.dumps(payload_obj).encode("utf-8")

        class _FakeResp:
            status = 200

            def read(self):
                return json.dumps({"id": "r1", "usage": {"prompt_tokens_details": {"cached_tokens": 100}}}).encode("utf-8")

            def getheaders(self):
                return [("Content-Type", "application/json")]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=30.0):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        handler = TokenSafeProxyHandler.__new__(TokenSafeProxyHandler)
        handler.headers = {"Content-Length": str(len(body)), "Authorization": "Bearer test-key"}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        with patch.dict(os.environ, {"TOKENSAFE_UPSTREAM_URL": "https://api.anthropic.com/v1/messages"}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            handler.handle_chat_completions()
        self.assertTrue(captured, "upstream request must be captured")
        return captured[0]

    def _payload(self):
        big_code = "\n".join(f"def fn_{i}(x):\n    return x * {i}" for i in range(120))
        return {
            "model": "claude-opus-4-6",
            "messages": [
                {"role": "system", "content": "System instruction block. " * 20},
                {"role": "user", "content": "start the refactor"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_old", "type": "function",
                     "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/main.py"})}},
                ]},
                {"role": "tool", "tool_call_id": "call_old", "content": big_code},
                {"role": "user", "content": "continue"},
                {"role": "assistant", "content": "interim " * 30},
                {"role": "user", "content": "finish it"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_recent", "type": "function",
                     "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/recent.py"})}},
                ]},
                {"role": "tool", "tool_call_id": "call_recent", "content": big_code},
                {"role": "assistant", "content": "done"},
            ],
        }

    def test_ledger_folds_aged_result_in_flight(self):
        payload = self._payload()
        sent = self._invoke(payload)
        raw = json.dumps(sent, ensure_ascii=False)
        self.assertIn(FOLD_MARKER, raw, "aged tool result must be folded in-flight")
        self.assertEqual(raw.count(FOLD_MARKER), 1, "recent result stays verbatim")
        # Upstream Anthropic shape: tool results live as tool_result blocks
        self.assertIn("tool_result", raw)

    def test_ledger_disabled_passthrough(self):
        payload = self._payload()
        with patch.dict(os.environ, {"TOKENSAFE_TOOL_LEDGER": "0"}):
            sent = self._invoke(payload)
        self.assertNotIn(FOLD_MARKER, json.dumps(sent, ensure_ascii=False))

    def test_tool_pruning_session_stable_in_pipeline(self):
        tools = [
            {"name": f"mcp_{i}_tool", "description": f"domain tool {i}", "input_schema": {"type": "object"}}
            for i in range(10)
        ]
        payload = self._payload()
        payload["tools"] = tools
        payload["messages"][1]["content"] = "work on mcp_2_tool and mcp_5_tool"
        with patch.dict(os.environ, {"TOKENSAFE_TOOL_PRUNING": "1", "TOKENSAFE_TOOL_PRUNE_KEEP": "4"}):
            sent1 = self._invoke(json.loads(json.dumps(payload)))
            sent2 = self._invoke(json.loads(json.dumps(payload)))
        names1 = {t["name"] for t in sent1["tools"]}
        names2 = {t["name"] for t in sent2["tools"]}
        self.assertEqual(len(names1), 4, "pool 10 pruned to KEEP=4")
        self.assertEqual(names1, names2, "subset must be session-stable for KV-cache")
        self.assertIn("mcp_2_tool", names1, "relevance score picks mentioned tools")

    def test_prune_miss_recorded(self):
        tools = [
            {"name": f"mcp_{i}_tool", "description": f"domain tool {i}", "input_schema": {"type": "object"}}
            for i in range(10)
        ]
        payload = self._payload()
        payload["tools"] = tools
        payload["messages"][1]["content"] = "work on mcp_2_tool and mcp_5_tool"

        captured = []
        body = json.dumps(payload).encode("utf-8")

        class _FakeRespWithMiss:
            status = 200

            def read(self):
                return json.dumps({"id": "r1", "choices": [{"message": {"tool_calls": [
                    {"function": {"name": "mcp_9_tool", "arguments": "{}"}}
                ]}}]}).encode("utf-8")

            def getheaders(self):
                return [("Content-Type", "application/json")]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=30.0):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _FakeRespWithMiss()

        from src.proxy import TokenSafeProxyHandler
        handler = TokenSafeProxyHandler.__new__(TokenSafeProxyHandler)
        handler.headers = {"Content-Length": str(len(body)), "Authorization": "Bearer test-key"}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        with patch.dict(os.environ, {
            "TOKENSAFE_UPSTREAM_URL": "https://api.openai.com/v1/chat/completions",
            "TOKENSAFE_TOOL_PRUNING": "1",
            "TOKENSAFE_TOOL_PRUNE_KEEP": "4",
        }), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            handler.handle_chat_completions()
        from src.metrics import MetricsTracker
        summary = MetricsTracker().get_summary()
        self.assertEqual(summary["breakdown"].get("TOOL_PRUNE_MISS", {}).get("count", 0), 1)


if __name__ == "__main__":
    unittest.main()
