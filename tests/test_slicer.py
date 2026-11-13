import unittest
import tempfile
import os
from src.slicer import ToolSlicer

class TestToolSlicer(unittest.TestCase):
    def setUp(self):
        # Create a mock large file
        self.temp_file = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".py")
        for i in range(1, 501):
            if i == 250:
                self.temp_file.write(f"def process_order_payments(order_id):\n    return True\n")
            else:
                self.temp_file.write(f"# line {i} comments and dummy implementation details\n")
        self.temp_file.flush()
        self.temp_file.close()

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_slicing_with_target_symbol(self):
        args = {"Path": self.temp_file.name}
        overwrite, metrics = ToolSlicer.optimize_read_file(args, context_hint="optimize process_order_payments")
        self.assertIn("StartLine", overwrite)
        self.assertIn("EndLine", overwrite)
        # Should focus around line 250
        self.assertLessEqual(overwrite["StartLine"], 250)
        self.assertGreaterEqual(overwrite["EndLine"], 250)
        self.assertGreater(metrics["saved_tokens"], 500)

if __name__ == "__main__":
    unittest.main()
