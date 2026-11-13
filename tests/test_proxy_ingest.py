"""
Unit + integration tests for the Anthropic /v1/messages ingest path (v3.0.0):
Claude Code points ANTHROPIC_BASE_URL at the proxy and speaks Anthropic shape.
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.proxy import (
    _OpenAISSEToAnthropicTranslator,
    _translate_anthropic_payload_to_openai,
    _translate_openai_json_to_anthropic,
    _translate_openai_error_body_to_anthropic,
)


class TestAnthropicPayloadTranslation(unittest.TestCase):
    def test_request_translation_roundtrip_shape(self):
        payload = {
            "model": "claude-opus-4-6",
            "max_tokens": 2048,
            "system": "You are helpful.",
            "stream": True,
            "tools": [{"name": "get_weather", "description": "Weather lookup",
                       "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}],
            "messages": [
                {"role": "user", "content": "Weather in Paris?"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "22C sunny"},
                ]},
            ],
        }
        out = _translate_anthropic_payload_to_openai(payload)
        self.assertEqual(out["model"], "claude-opus-4-6")
        self.assertEqual(out["max_tokens"], 2048)
        self.assertTrue(out["stream"])
        self.assertEqual(out["messages"][0]["role"], "system")
        self.assertEqual(out["messages"][1]["content"], "Weather in Paris?")
        assistant = out["messages"][2]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["content"], "Checking.")
        self.assertEqual(assistant["tool_calls"][0]["id"], "toolu_1")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"]), {"city": "Paris"})
        tool_result = out["messages"][3]
        self.assertEqual(tool_result["role"], "tool")
        self.assertEqual(tool_result["tool_call_id"], "toolu_1")
        self.assertEqual(tool_result["content"], "22C sunny")
        self.assertEqual(out["tools"][0]["function"]["name"], "get_weather")
        self.assertEqual(out["tools"][0]["function"]["parameters"]["properties"]["city"]["type"], "string")

    def test_response_translation_to_anthropic(self):
        openai_resp = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "It is 22C.",
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
        out = _translate_openai_json_to_anthropic(openai_resp)
        self.assertEqual(out["type"], "message")
        self.assertEqual(out["role"], "assistant")
        self.assertEqual(out["stop_reason"], "tool_use")
        self.assertEqual(out["usage"]["input_tokens"], 120)
        kinds = [b["type"] for b in out["content"]]
        self.assertEqual(kinds, ["text", "tool_use"])
        self.assertEqual(out["content"][1]["input"], {"city": "Paris"})

    def test_error_envelope_translation(self):
        out_bytes = _translate_openai_error_body_to_anthropic(
            b'{"error": {"message": "Overloaded", "type": "server_error"}}'
        )
        out = json.loads(out_bytes)
        self.assertEqual(out["type"], "error")
        self.assertEqual(out["error"]["message"], "Overloaded")
        # Anthropic envelope passes through untouched
        same = b'{"type": "error", "error": {"type": "api_error", "message": "boom"}}'
        self.assertEqual(_translate_openai_error_body_to_anthropic(same), same)


class TestOpenAISSEToAnthropic(unittest.TestCase):
    def test_stream_translation(self):
        t = _OpenAISSEToAnthropicTranslator("gpt-4o-mini")
        chunks = (
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get_weather","arguments":""}}]}}]}\n\n'
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"Par"}}]}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            b"data: [DONE]\n\n"
        )
        out = t.feed(chunks).decode("utf-8")
        self.assertIn("event: message_start", out)
        self.assertIn("event: content_block_start", out)
        self.assertIn('"text_delta"', out)
        self.assertIn("input_json_delta", out)
        self.assertIn('"stop_reason": "tool_use"', out)
        self.assertIn("event: message_stop", out)
        self.assertEqual(t.flush(), b"", "stream already closed by [DONE]")


class TestIngestEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tokensafe_ingest_")
        self.addCleanup(__import__("shutil").rmtree, self._tmp, ignore_errors=True)
        os.environ["TOKENSAFE_DB_PATH"] = os.path.join(self._tmp, "metrics.db")
        os.environ["TOKENSAFE_TELEMETRY_DISABLED"] = "1"
        self.addCleanup(os.environ.pop, "TOKENSAFE_DB_PATH", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_TELEMETRY_DISABLED", None)
        os.environ.pop("TOKENSAFE_TOOL_LEDGER", None)
        os.environ.pop("TOKENSAFE_UPSTREAM_MODEL", None)
        self.addCleanup(os.environ.pop, "TOKENSAFE_UPSTREAM_MODEL", None)
        from src import metrics as metrics_mod
        metrics_mod._INITIALIZED_METRICS_DBS.clear()

    def test_messages_endpoint_end_to_end(self):
        from src.proxy import TokenSafeProxyHandler
        captured = []
        anthropic_body = json.dumps({
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "system": "Be terse.",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "read_thing", "input": {"path": "/tmp/x"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "contents here"}]},
            ],
        }).encode("utf-8")

        class _FakeResp:
            status = 200

            def read(self):
                # Anthropic upstream answers in Anthropic shape -> client is
                # Anthropic too, so the body must pass through untranslated.
                return json.dumps({
                    "id": "msg_1", "type": "message", "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [{"type": "text", "text": "Hi there"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }).encode("utf-8")

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
        handler.path = "/v1/messages"
        handler.headers = {"Content-Length": str(len(anthropic_body)), "x-api-key": "sk-ant-test"}
        handler.rfile = io.BytesIO(anthropic_body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        with patch.dict(os.environ, {"TOKENSAFE_UPSTREAM_URL": "https://api.anthropic.com/v1/messages"}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            handler.do_POST()

        self.assertEqual(len(captured), 1)
        sent = captured[0]
        # Outbound is Anthropic shape for the Anthropic upstream (system top-level)
        self.assertIn("system", sent)
        self.assertEqual(sent["messages"][0]["content"], "hello")
        tool_use_blocks = [b for m in sent["messages"] for b in (m["content"] if isinstance(m["content"], list) else []) if isinstance(b, dict) and b.get("type") == "tool_use"]
        self.assertTrue(tool_use_blocks, "assistant tool_use preserved outbound")
        # Response passthrough written to the Anthropic client untouched in shape
        written = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Hi there", written)
        self.assertIn('"type": "message"', written)


if __name__ == "__main__":
    unittest.main()
