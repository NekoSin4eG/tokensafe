import unittest
import tempfile
import os
from src.benchmark import BenchmarkSuite

class TestBenchmarkSuite(unittest.TestCase):
    def test_benchmark_runs_and_verifies_zero_loss(self):
        data = BenchmarkSuite.run_all()
        self.assertIn("tasks", data)
        self.assertGreaterEqual(len(data["tasks"]), 5)
        self.assertTrue(data["zero_loss_verified"])
        self.assertGreater(data["net_reduction_pct"], 80.0)
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
        self.assertIn("SWE-09", content)
        self.assertIn("SWE-10", content)
        self.assertIn("Multi-Turn KV-Cache & Provider Bill Simulation", content)
        if os.path.exists(temp_md.name):
            os.remove(temp_md.name)

    def test_v2_scenarios_kv_cache_and_edge_cases(self):
        from src.distiller_cache import DistillationCache

        prev_db = DistillationCache._db_path
        prev_hits = DistillationCache._hits
        prev_misses = DistillationCache._misses

        data = BenchmarkSuite.run_all()
        ids = {t["id"] for t in data["tasks"]}
        self.assertTrue({"SWE-06", "SWE-07", "SWE-09", "SWE-10"}.issubset(ids))
        self.assertEqual(
            data["total_opt_measured_tokens"] + data["total_opt_modeled_tokens"],
            data["total_opt_tokens"],
        )
        self.assertGreater(data["total_opt_measured_tokens"], 500)
        self.assertGreaterEqual(data["peak_scenario_reduction_pct"], 98.0)

        kv = data.get("kv_cache_benchmark", {})
        self.assertGreater(kv.get("turns", 0), 10)
        self.assertGreater(kv.get("kv_cache_hit_rate_pct", 0.0), 80.0)
        self.assertGreater(kv.get("claude_total_savings_vs_uncached_pct", 0.0), 90.0)
        self.assertGreater(kv.get("claude_total_savings_vs_cached_midtrim_pct", 0.0), 70.0)

        # Edge case 1: run_all preserves DistillationCache state without leaking test entries
        self.assertEqual(DistillationCache._db_path, prev_db)
        self.assertEqual(DistillationCache._hits, prev_hits)
        self.assertEqual(DistillationCache._misses, prev_misses)

        # Edge case 2: empty package directory returns {} safely without dividing by zero
        with tempfile.TemporaryDirectory() as empty_dir:
            self.assertEqual(BenchmarkSuite.run_kv_cache_benchmark(pkg_dir=empty_dir), {})

if __name__ == "__main__":
    unittest.main()
