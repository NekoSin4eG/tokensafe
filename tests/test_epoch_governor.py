import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from src.governor import ContextGovernor, ContextBudget


class TestEpochGovernor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_epoch_")
        self._old_db = os.environ.get("TOKENSAFE_DB_PATH")
        self._old_dir = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = self._tmp
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")

    def tearDown(self):
        import shutil
        if self._old_db is None:
            os.environ.pop("TOKENSAFE_DB_PATH", None)
        else:
            os.environ["TOKENSAFE_DB_PATH"] = self._old_db
        if self._old_dir is None:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
        else:
            os.environ["TOKENSAFE_DATA_DIR"] = self._old_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_resolve_epoch_limit_hardcoded_80_pct_of_model_window(self):
        old = os.environ.pop("TOKENSAFE_CONTEXT_WINDOW", None)
        try:
            self.assertEqual(ContextGovernor.resolve_epoch_limit("gemini-3.8-flash"), 800_000)
            self.assertEqual(ContextGovernor.resolve_epoch_limit("claude-opus-4.6"), 160_000)
            self.assertEqual(ContextGovernor.resolve_epoch_limit("gpt-5"), 102_400)
            self.assertEqual(ContextGovernor.resolve_epoch_limit(""), 160_000)
        finally:
            if old is not None:
                os.environ["TOKENSAFE_CONTEXT_WINDOW"] = old

    def test_append_only_preserves_prefix_bytes_on_tail_fold_and_triggers_rollover(self):
        long_py = "def fn():\n" + "".join(f"    v_{i} = {i}\n" for i in range(40))
        msgs = [
            {"role": "system", "content": "Static system prompt."},
            {"role": "user", "content": "First user turn"},
            {"role": "assistant", "content": "First assistant reply"},
            {"role": "user", "content": "Second user turn"},
            {"role": "assistant", "content": "Second assistant reply"},
            {"role": "user", "content": f"New tail:\n```python\n{long_py}\n```"},
        ]
        # Force a small epoch limit via ContextBudget.resolve_window patch
        with patch.object(ContextBudget, "resolve_window", return_value=200):
            # 80% of 200 = 160 tokens -> tail fold runs, historical msgs[0..4] untouched
            governed, stats = ContextGovernor.govern_messages(msgs, append_only=True)
        self.assertIn("ast_fold_tail_code", stats["actions"])
        self.assertEqual(governed[:5], msgs[:5])

        # With tiny window (epoch limit 32 -> over budget even after tail fold) & >6 msgs -> atomic Epoch Rollover
        msgs_long = [
            {"role": "system", "content": "Static system prompt."},
            {"role": "user", "content": "First user turn details " * 8},
            {"role": "assistant", "content": "First assistant reply details " * 8},
            {"role": "user", "content": "Second user turn details " * 8},
            {"role": "assistant", "content": "Second assistant reply details " * 8},
            {"role": "user", "content": "Turn 3"},
            {"role": "assistant", "content": "Reply 3"},
            {"role": "user", "content": "Turn 4"},
        ]
        with patch.object(ContextBudget, "resolve_window", return_value=40):
            governed2, stats2 = ContextGovernor.govern_messages(msgs_long, append_only=True)
        self.assertTrue(stats2["epoch_rollover"])
        self.assertIn("epoch_rollover", stats2["actions"])
        self.assertIn("[Epoch Ledger:", governed2[1]["content"])
        self.assertEqual(governed2[0], msgs_long[0])
        self.assertEqual(governed2[-3:], msgs_long[-3:])

    def test_proxy_pipeline_preserves_cache_prefix_and_runs_govern_before_normalize(self):
        from src.proxy import TokenSafeProxyHandler

        captured_payloads = []

        class _FakeResp:
            status = 200
            def read(self):
                return json.dumps({
                    "id": "r1",
                    "usage": {"cache_read_input_tokens": 1200}
                }).encode("utf-8")
            def getheaders(self):
                return [("Content-Type", "application/json")]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=30.0):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        def invoke_proxy(payload_obj):
            body = json.dumps(payload_obj).encode("utf-8")
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

        sys_text = "System instruction block. " * 250
        tools = [
            {"name": "b_tool", "input_schema": {"z": 1, "a": 2}},
            {"name": "a_tool", "input_schema": {"b": 1, "a": 2}},
        ]
        invoke_proxy({
            "model": "claude-opus-4-6",
            "tools": tools,
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": "Turn 1"},
            ],
        })
        invoke_proxy({
            "model": "claude-opus-4-6",
            "tools": tools,
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Answer 1 " * 80},
                {"role": "user", "content": "Turn 2"},
            ],
        })

        self.assertEqual(len(captured_payloads), 2)
        p1, p2 = captured_payloads
        # Tools sorted deterministically + cache_control on last tool
        self.assertEqual([t["name"] for t in p1["tools"]], ["a_tool", "b_tool"])
        self.assertEqual(p1["tools"][-1]["cache_control"], {"type": "ephemeral"})
        # System prefix bytes identical across turns (KV-cache preserved)
        self.assertEqual(p1["messages"][0], p2["messages"][0])
        self.assertEqual(p1["messages"][1], p2["messages"][1])
        # Rolling breakpoint placed on penultimate turn in p2
        self.assertEqual(p2["messages"][2]["content"][0]["cache_control"], {"type": "ephemeral"})

    def test_epoch_short_history_edge_case_and_openai_proxy_route(self):
        from src.proxy import TokenSafeProxyHandler

        # Short session (<= 6 msgs) over tiny epoch limit: cannot mid-consolidate <4 msgs cleanly without crash
        short_msgs = [
            {"role": "system", "content": "Static prompt."},
            {"role": "user", "content": "Very verbose user prompt " * 20},
            {"role": "assistant", "content": "Short reply."},
        ]
        with patch.object(ContextBudget, "resolve_window", return_value=20):
            gov_short, st_short = ContextGovernor.govern_messages(short_msgs, append_only=True)
        self.assertFalse(st_short["epoch_rollover"])
        self.assertEqual(len(gov_short), 3)

        # OpenAI upstream route keeps string system prompt and injects deterministic prompt_cache_key
        captured = []

        class _FakeResp:
            status = 200
            def read(self):
                return json.dumps({"id": "o1", "usage": {"cached_tokens": 800}}).encode("utf-8")
            def getheaders(self):
                return [("Content-Type", "application/json")]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        body = json.dumps({
            "model": "gpt-4.1",
            "messages": [
                {"role": "system", "content": "System instructions. " * 250},
                {"role": "user", "content": "Turn 1"},
            ],
        }).encode("utf-8")
        handler = TokenSafeProxyHandler.__new__(TokenSafeProxyHandler)
        handler.headers = {"Content-Length": str(len(body)), "Authorization": "Bearer test-key"}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        with patch.dict(os.environ, {"TOKENSAFE_UPSTREAM_URL": "https://api.openai.com/v1/chat/completions"}), \
             patch("urllib.request.urlopen", side_effect=lambda req, timeout=30.0: (captured.append(json.loads(req.data.decode("utf-8"))), _FakeResp())[1]):
            handler.handle_chat_completions()

        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0]["messages"][0]["content"], str)
        self.assertTrue(captured[0].get("prompt_cache_key"))


if __name__ == "__main__":
    unittest.main()
