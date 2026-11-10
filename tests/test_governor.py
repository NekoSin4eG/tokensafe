import unittest
from src.governor import ContextGovernor

class TestContextGovernor(unittest.TestCase):
    def test_governor_passes_under_budget(self):
        msgs = [
            {"role": "system", "content": "Short system prompt."},
            {"role": "user", "content": "Short question."}
        ]
        governed, stats = ContextGovernor.govern_messages(msgs, max_tokens=1000)
        self.assertFalse(stats["throttled"])
        self.assertEqual(len(governed), 2)

    def test_governor_folds_large_code_blocks(self):
        long_py_code = "def large_fn():\n" + "".join([f"    x_{i} = {i} * 2\n" for i in range(50)])
        msgs = [
            {"role": "assistant", "content": f"Here is the code:\n```python\n{long_py_code}\n```"},
            {"role": "user", "content": "Now please add tests."}
        ]
        governed, stats = ContextGovernor.govern_messages(msgs, max_tokens=50, target_tokens=40)
        self.assertTrue(stats["throttled"])
        self.assertIn("ast_fold_code_blocks", stats["actions"])
        self.assertIn("AST-folded", governed[0]["content"])
        self.assertGreater(stats["saved_tokens"], 0)

    def test_governor_compacts_history_when_over_budget(self):
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Пожалуйста перепиши функцию get_balance в wallet.ts и добавь туда redis кэш"},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Next step."}
        ]
        governed, stats = ContextGovernor.govern_messages(msgs, max_tokens=20, target_tokens=15)
        self.assertTrue(stats["throttled"])
        # Intermediate user prompt should be compacted to TOCP
        self.assertIn("[TOCP Intent]:", governed[1]["content"])

if __name__ == "__main__":
    unittest.main()
