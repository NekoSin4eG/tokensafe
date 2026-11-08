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

if __name__ == "__main__":
    unittest.main()
