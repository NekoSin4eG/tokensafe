import unittest

from src.json_repair import parse_tool_arguments, repair_json


class TestJsonRepair(unittest.TestCase):
    """Self-healing tool arguments: broken JSON from small/local models gets
    repaired instead of burning a retry roundtrip."""

    def _d(self, raw):
        val = parse_tool_arguments(raw)
        self.assertIsInstance(val, dict, f"expected dict for {raw!r}")
        return val

    def test_valid_json_passthrough(self):
        self.assertEqual(self._d('{"path": "/tmp/x", "depth": 2}'), {"path": "/tmp/x", "depth": 2})

    def test_dict_passthrough(self):
        self.assertEqual(self._d({"path": "/tmp"}), {"path": "/tmp"})

    def test_trailing_comma(self):
        self.assertEqual(self._d('{"path": "/tmp/x",}'), {"path": "/tmp/x"})

    def test_missing_closing_brace(self):
        self.assertEqual(self._d('{"path": "/tmp/x"'), {"path": "/tmp/x"})

    def test_missing_nested_closers(self):
        self.assertEqual(self._d('{"a": {"b": [1, 2'), {"a": {"b": [1, 2]}})

    def test_python_literals(self):
        val = self._d('{"flag": True, "off": False, "val": None}')
        self.assertEqual(val, {"flag": True, "off": False, "val": None})
        self.assertIn("true", repair_json('{"flag": True}'))

    def test_raw_newline_inside_string(self):
        val = self._d('{"cmd": "echo hi\nbye"}')
        self.assertEqual(val["cmd"], "echo hi\nbye")

    def test_unterminated_string(self):
        val = self._d('{"path": "/tmp')
        self.assertEqual(val, {"path": "/tmp"})

    def test_single_quoted_when_no_double_quotes(self):
        self.assertEqual(self._d("{'path': '/tmp/x'}"), {"path": "/tmp/x"})

    def test_bom_prefix(self):
        self.assertEqual(self._d('\ufeff{"path": "/tmp"}'), {"path": "/tmp"})

    def test_unrepairable_garbage_returns_none(self):
        self.assertIsNone(parse_tool_arguments("not json at all"))
        self.assertIsNone(parse_tool_arguments(""))
        self.assertIsNone(parse_tool_arguments(None))
        self.assertIsNone(parse_tool_arguments("[1, 2, 3]"))  # non-object arguments
        self.assertIsNone(parse_tool_arguments('{"a": ]}'))  # unbalanceable

    def test_whitespace_only(self):
        self.assertIsNone(parse_tool_arguments("   "))

    def test_proxy_tool_call_translation_repairs_arguments(self):
        from src.proxy import _translate_openai_payload_to_anthropic

        payload = {
            "model": "claude-test",
            "messages": [{
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path": "/tmp",'},
                }],
            }],
        }
        out = _translate_openai_payload_to_anthropic(payload)
        blocks = out["messages"][0]["content"]
        tool_use = [b for b in blocks if b.get("type") == "tool_use"]
        self.assertEqual(len(tool_use), 1)
        self.assertEqual(tool_use[0]["input"], {"path": "/tmp"})

    def test_proxy_unrepairable_arguments_keep_empty_input(self):
        from src.proxy import _translate_openai_payload_to_anthropic

        payload = {
            "model": "claude-test",
            "messages": [{
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "garbage {"},
                }],
            }],
        }
        out = _translate_openai_payload_to_anthropic(payload)
        blocks = out["messages"][0]["content"]
        tool_use = [b for b in blocks if b.get("type") == "tool_use"]
        self.assertEqual(tool_use[0]["input"], {})


if __name__ == "__main__":
    unittest.main()


import json


class TestTruncationRepair(unittest.TestCase):
    """QA sweep finding: repair_json returned None on payloads cut MID-TOKEN
    (no closer appender can fix '{"a": tru') — a truncation-boundary stage was
    added as the last-resort candidate."""

    def test_cut_mid_literal(self):
        r = repair_json('{"count": 42, "flag": tru')
        self.assertIsNotNone(r)
        self.assertEqual(sorted(json.loads(r).keys()), ["count", "flag"])

    def test_cut_after_colon(self):
        r = repair_json('{"a": 1, "b": ')
        self.assertIsNotNone(r)
        self.assertEqual(sorted(json.loads(r).keys()), ["a", "b"])

    def test_cut_mid_key(self):
        r = repair_json('{"na')
        self.assertIsNotNone(r)
        self.assertEqual(json.loads(r), {})

    def test_cut_mid_string_with_escape(self):
        r = repair_json('{"a": "x\\')
        self.assertIsNotNone(r)
        self.assertEqual(json.loads(r), {"a": None})  # cut past the dangling colon keeps the key

    def test_cut_mid_array_literal(self):
        r = repair_json('{"items": [1, 2, tru')
        self.assertIsNotNone(r)
        self.assertEqual(json.loads(r)["items"], [1, 2])

    def test_plain_number_truncation_keeps_value(self):
        r = repair_json('{"count": 12')
        self.assertIsNotNone(r)
        self.assertEqual(json.loads(r), {"count": 12})
