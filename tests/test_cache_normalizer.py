import unittest
from src.cache_normalizer import PromptCacheNormalizer

class TestCacheNormalizer(unittest.TestCase):
    def test_strip_volatile_elements(self):
        text = (
            "System prompt.\n"
            "The current local time is: 2026-11-08T19:09:31+02:00.\n"
            "turn_count=12 request_id='req-9912'\n"
            "Base instructions continue."
        )
        cleaned = PromptCacheNormalizer.strip_volatile_elements(text)
        self.assertNotIn("The current local time", cleaned)
        self.assertNotIn("turn_count=12", cleaned)
        self.assertNotIn("req-9912", cleaned)
        self.assertIn("Base instructions continue.", cleaned)

    def test_normalize_messages_and_hash(self):
        msgs1 = [
            {"role": "system", "content": "You are a coding assistant.\nThe current local time is: 2026-11-08T12:00:00."},
            {"role": "user", "content": "Hello"}
        ]
        msgs2 = [
            {"role": "system", "content": "You are a coding assistant.\nThe current local time is: 2026-11-08T18:45:00."},
            {"role": "user", "content": "Different user prompt"}
        ]
        norm1, meta1 = PromptCacheNormalizer.normalize_messages(msgs1)
        norm2, meta2 = PromptCacheNormalizer.normalize_messages(msgs2)

        # Static prefix hash must match 100% despite volatile timestamps!
        self.assertEqual(meta1["prefix_hash"], meta2["prefix_hash"])
        self.assertTrue(len(meta1["prefix_hash"]) > 20)

    def test_anthropic_cache_control_injection(self):
        large_system = "You are a senior system engineer. " * 300 # ~1200 tokens
        msgs = [
            {"role": "system", "content": large_system},
            {"role": "user", "content": "Run tests"}
        ]
        norm, meta = PromptCacheNormalizer.normalize_messages(msgs)
        self.assertTrue(meta["cache_control_injected"])
        self.assertIsInstance(norm[0]["content"], list)
        self.assertEqual(norm[0]["content"][0]["cache_control"]["type"], "ephemeral")

    def test_strips_hit_counters_and_session_metadata(self):
        text = (
            "// [TokenSafe Subagent Firewall: Reused session read for 'a.py' (Dedup Hit #3, Saved ~420 tokens)]\n"
            "Conversation ID: 12345678-1234-1234-1234-1234567890ab\n"
            "invocationNum=9\n"
            "Core instructions."
        )
        cleaned = PromptCacheNormalizer.strip_volatile_elements(text)
        self.assertNotIn("Dedup Hit #3", cleaned)
        self.assertNotIn("12345678-1234-1234-1234-1234567890ab", cleaned)
        self.assertNotIn("invocationNum=9", cleaned)
        self.assertIn("Core instructions.", cleaned)

    def test_anthropic_multi_breakpoint_and_canonical_tools(self):
        large_system = "You are a senior system engineer. " * 300
        tools = [
            {"name": "zeta_tool", "input_schema": {"b": 2, "a": 1}},
            {"name": "alpha_tool", "input_schema": {"z": 9, "y": 8}},
        ]
        msgs = [
            {"role": "system", "content": large_system},
            {"role": "user", "content": "Inspect auth.py"},
            {"role": "assistant", "content": "Checked auth.py " * 100},
            {"role": "user", "content": "Now patch it"},
        ]
        norm, meta = PromptCacheNormalizer.normalize_messages(msgs, provider="anthropic", tools=tools)
        self.assertEqual(meta["breakpoints_injected"], 3)
        norm_tools = meta["normalized_tools"]
        self.assertEqual([t["name"] for t in norm_tools], ["alpha_tool", "zeta_tool"])
        self.assertEqual(list(norm_tools[0]["input_schema"].keys()), ["y", "z"])
        self.assertEqual(norm_tools[-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(norm[0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(norm[2]["content"][0]["cache_control"], {"type": "ephemeral"})

    def test_openai_avoids_cache_control_and_emits_cache_key(self):
        large_system = "You are a senior system engineer. " * 300
        msgs = [
            {"role": "system", "content": large_system},
            {"role": "user", "content": "Inspect auth.py"},
        ]
        norm, meta = PromptCacheNormalizer.normalize_messages(msgs, provider="openai")
        self.assertFalse(meta["cache_control_injected"])
        self.assertIsInstance(norm[0]["content"], str)
        self.assertTrue(meta.get("prompt_cache_key"))

    def test_empty_messages_markdown_saved_and_list_blocks(self):
        norm_empty, meta_empty = PromptCacheNormalizer.normalize_messages([])
        self.assertEqual(norm_empty, [])
        self.assertEqual(meta_empty["breakpoints_injected"], 0)

        cleaned = PromptCacheNormalizer.strip_volatile_elements(
            "Saved **1,250 tokens**\nGenerated locally at 2026-11-12 04:30:00\nKeep me"
        )
        self.assertNotIn("1,250 tokens", cleaned)
        self.assertNotIn("Generated locally at", cleaned)
        self.assertIn("Keep me", cleaned)

        msgs_small = [
            {"role": "system", "content": [{"type": "text", "text": "Sys rule " * 250}]},
            {"role": "user", "content": [{"type": "text", "text": "Fix bug"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Working"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ]
        _, meta_small = PromptCacheNormalizer.normalize_messages(msgs_small, provider="anthropic")
        # Below Anthropic's 1024-token cache minimum -> 0 breakpoints injected
        self.assertEqual(meta_small["breakpoints_injected"], 0)

        msgs = [
            {"role": "system", "content": [{"type": "text", "text": "You are a senior engineer checking rules. " * 200}]},
            {"role": "user", "content": [{"type": "text", "text": "Fix bug"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Working " * 120}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ]
        norm, meta = PromptCacheNormalizer.normalize_messages(msgs, provider="anthropic")
        self.assertEqual(meta["breakpoints_injected"], 2)
        self.assertEqual(norm[0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(norm[2]["content"][0]["cache_control"], {"type": "ephemeral"})
        # Caller's original message list blocks remain untouched (no in-place mutation)
        self.assertNotIn("cache_control", msgs[0]["content"][0])
        self.assertNotIn("cache_control", msgs[2]["content"][0])

if __name__ == "__main__":
    unittest.main()
