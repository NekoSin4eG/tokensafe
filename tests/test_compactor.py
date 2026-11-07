import unittest
from src.compactor import PromptCompactor

class TestPromptCompactor(unittest.TestCase):
    def test_russian_compaction(self):
        prompt = "Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной"
        res = PromptCompactor.compact(prompt)
        self.assertEqual(res["action"], "REFACTOR")
        self.assertEqual(res["target_file"], "wallet.ts")
        self.assertEqual(res["symbol"], "get_balance")
        self.assertIn("async", res["traits"])
        self.assertIn("redis", res["traits"])
        self.assertGreater(res["saved_tokens"], 15)
        self.assertGreater(res["reduction_pct"], 35.0)

    def test_english_compaction(self):
        prompt = "Please refactor the function calculate_tax in src/billing.py to be async and cache in redis"
        res = PromptCompactor.compact(prompt)
        self.assertEqual(res["action"], "REFACTOR")
        self.assertEqual(res["target_file"], "src/billing.py")
        self.assertEqual(res["symbol"], "calculate_tax")
        self.assertIn("async", res["traits"])
        self.assertGreaterEqual(res["saved_tokens"], 5)

    def test_short_command_passthrough(self):
        # Short CLI commands must pass through untouched
        prompt = "git status"
        res = PromptCompactor.compact(prompt)
        self.assertEqual(res["compact_text"], "git status")
        self.assertEqual(res["saved_tokens"], 0)

if __name__ == "__main__":
    unittest.main()
