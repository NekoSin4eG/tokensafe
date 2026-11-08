import unittest
import tempfile
import os
from src.benchmark import BenchmarkSuite

class TestBenchmarkSuite(unittest.TestCase):
    def test_benchmark_runs_and_verifies_zero_loss(self):
        data = BenchmarkSuite.run_all()
        self.assertIn("tasks", data)
        self.assertEqual(len(data["tasks"]), 5)
        self.assertTrue(data["zero_loss_verified"])
        self.assertGreater(data["net_reduction_pct"], 60.0)
        self.assertGreaterEqual(data["speedup_pct"], 10.0)
        self.assertGreater(data["total_saved_tokens"], 10000)

    def test_benchmark_report_rendering(self):
        data = BenchmarkSuite.run_all()
        report = BenchmarkSuite.render_report(data)
        self.assertIn("ZERO-LOSS SWE-BENCH BENCHMARK REPORT", report)
        self.assertIn("PASSED (100% Task Success Rate)", report)

    def test_markdown_export(self):
        data = BenchmarkSuite.run_all()
        temp_md = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md")
        temp_md.close()
        BenchmarkSuite.export_markdown_report(data, temp_md.name)
        with open(temp_md.name, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# 🏆 Zero-Loss SWE-bench Benchmark Report", content)
        self.assertIn("SWE-01", content)
        if os.path.exists(temp_md.name):
            os.remove(temp_md.name)

if __name__ == "__main__":
    unittest.main()
