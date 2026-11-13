"""
Unit tests for the Real-Repo Benchmark harness (no network):
- pre-registered registry loads and is well-formed (anti-cherry-pick contract)
- symbol-read measurement + zero-loss gates on a temp fixture file
- test-output gates on real-shaped pytest output
- markdown rendering includes measured rows only
"""

import json
import os
import tempfile
import unittest

from src.benchmark_real import (
    RealRepoBenchmark,
    load_registry,
    symbol_read_gates,
    test_output_gates,
    REGISTRY_PATH,
)

FAT_SOURCE = (
    "import json\n"
    + "".join(
        f"\n\ndef helper_{i}(a, b):\n" + "".join(f"    acc_{j} = a + b + {j}\n" for j in range(15)) + f"    return acc_14 + {i}\n"
        for i in range(19)
    )
    + "\n\nclass Engine:\n"
    "    def __init__(self):\n"
    "        self.state = {}\n"
    "\n"
    "    def pulse(self):\n"
    "        return bool(self.state)\n"
    "\n\n"
    "def big_target(n):\n"
    + "".join(f"    step_{i} = n + {i}\n" for i in range(200))
    + "    return step_199\n"
)


class TestRegistryPreRegistration(unittest.TestCase):
    def test_registry_loads_and_is_well_formed(self):
        reg = load_registry()
        self.assertEqual(reg.get("version"), 1)
        self.assertGreaterEqual(len(reg["repos"]), 5, "pre-registration promises 5 repos")
        for repo in reg["repos"]:
            self.assertIn("url", repo)
            self.assertIn("symbol", repo["symbol_read"])
            self.assertIn("file", repo["symbol_read"])
            self.assertGreaterEqual(len(repo.get("multiturn_reads", [])), 3)
            self.assertIn("deps", repo.get("pytest", {}))
        self.assertIn("zero_loss_gates", reg)
        # Gates promised per scenario must exist in the implementation
        sample = symbol_read_gates("def x():\n    return 1\n", "def x():\n    return 1\n", "x")
        for gate in reg["zero_loss_gates"]["symbol_read"]:
            self.assertIn(gate, sample)
        for gate in reg["zero_loss_gates"]["test_output"]:
            self.assertIn(gate, test_output_gates("x"))

    def test_registry_file_is_committed_artifact(self):
        self.assertTrue(os.path.isfile(REGISTRY_PATH))


class TestSymbolReadGates(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(FAT_SOURCE)
        self.full_text = FAT_SOURCE

    def tearDown(self):
        os.remove(self.path)

    def test_skeleton_measured_and_lossless(self):
        bench = RealRepoBenchmark()
        res = bench.measure_symbol_read(os.path.dirname(self.path), os.path.basename(self.path), "big_target")
        self.assertEqual(res["status"], "ok", res.get("reason"))
        self.assertGreater(res["reduction_pct"], 50.0, "400-line folded body must save >50%")
        self.assertGreater(res["usd_saved"], 0.0)
        for gate, ok in res["gates"].items():
            self.assertTrue(ok, f"gate failed: {gate}")

    def test_gate_catches_missing_symbol(self):
        fake_skel = "def other_thing():\n    return 1\n"
        gates = symbol_read_gates(self.full_text, fake_skel, "big_target")
        self.assertFalse(gates["symbol_present"])
        self.assertFalse(gates["all_top_level_defs_present"])

    def test_gate_catches_truncated_signature(self):
        fake_skel = "def big_target(n, extra=None):\n    return 0\n"
        gates = symbol_read_gates(self.full_text, fake_skel, "big_target")
        self.assertFalse(gates["signature_line_match"])


class TestTestOutputGates(unittest.TestCase):
    RAW = (
        "============================ short test summary info ============================\n"
        "FAILED tests/test_bench_injected.py::test_bench_injected_regression - AssertionError: bench: pre-registered injected regression\n"
        "========================= 1 failed, 12 passed in 0.42s =========================\n"
        "=================================== FAILURES ===================================\n"
        "________________________________ test_bench_injected_regression ________________\n"
        "tests/test_bench_injected.py:2: in test_bench_injected_regression\n"
        "    assert False, \"bench: pre-registered injected regression\"\n"
        "E   AssertionError: bench: pre-registered injected regression\n"
    )

    def test_gates_pass_on_real_shaped_output(self):
        gates = test_output_gates(self.RAW)
        self.assertTrue(all(gates.values()))

    def test_gates_fail_when_info_stripped(self):
        gates = test_output_gates("1 failed in 0.42s")
        self.assertFalse(all(gates.values()))
        self.assertFalse(gates["failing_test_name_present"])


class TestMarkdownRendering(unittest.TestCase):
    def test_report_rows_render(self):
        bench = RealRepoBenchmark()
        data = {
            "methodology": "measured",
            "results": [{
                "key": "fixture",
                "url": "local",
                "symbol_read": {"status": "ok", "baseline_tokens": 1000, "optimized_tokens": 200,
                                "saved_tokens": 800, "reduction_pct": 80.0, "usd_saved": 0.0024,
                                "gates": {"symbol_present": True}},
            }],
        }
        md = bench.render_markdown(data)
        self.assertIn("| fixture | symbol_read |", md)
        self.assertIn("80.0%", md)
        self.assertIn("| PASS |", md)


if __name__ == "__main__":
    unittest.main()
