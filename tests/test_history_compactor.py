import unittest
import tempfile
import json
import os
from src.history_compactor import HistoryCompactor

class TestHistoryCompactor(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".jsonl")
        # Write dummy multi-turn transcript steps
        for i in range(1, 10):
            step = {
                "step_index": i,
                "type": "PLANNER_RESPONSE",
                "content": f"Exploring step {i} with lots of context and repeated details " * 20,
                "tool_calls": [
                    {"name": "read_file", "args": {"Path": f"/app/src/service_{i % 3}.ts"}},
                    {"name": "run_command", "args": {"CommandLine": "npm test"}}
                ]
            }
            self.temp_file.write(json.dumps(step) + "\n")
        self.temp_file.flush()
        self.temp_file.close()

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_fold_transcript(self):
        folded_text, orig_t, fold_t, stats = HistoryCompactor.fold_transcript(
            self.temp_file.name, invocation_num=5
        )
        self.assertIsNotNone(folded_text)
        self.assertIn("[TOCP State Ledger]:", folded_text)
        self.assertIn("service_0.ts", folded_text)
        self.assertGreater(stats["saved_tokens"], 500)
        self.assertLess(fold_t, orig_t)

    def test_early_turns_skip(self):
        # Turns < 3 must not fold
        folded_text, orig_t, fold_t, stats = HistoryCompactor.fold_transcript(
            self.temp_file.name, invocation_num=2
        )
        self.assertIsNone(folded_text)

if __name__ == "__main__":
    unittest.main()
