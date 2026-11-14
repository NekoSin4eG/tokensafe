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

from src.benchmark_real import RealRepoBenchmark as _RRB

RAW_SEARCH_SAMPLE = "\n".join(f"tests/test_mod_{i}.py:{i * 3}:    def test_case_{i}(): ..." for i in range(40))
RAW_DIFF_SAMPLE = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,4 @@\n"
    " def main():\n"
    "+    return 2\n"
    "diff --git a/poetry.lock b/poetry.lock\n"
    "--- a/poetry.lock\n"
    "+++ b/poetry.lock\n"
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
        self.assertGreaterEqual(reg.get("version"), 1)
        self.assertGreaterEqual(len(reg["repos"]), 5, "pre-registration promises 5 repos")
        for repo in reg["repos"]:
            self.assertIn("url", repo)
            if repo.get("auto"):
                self.assertIn("language", repo)
                continue
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


class TestNewScenarioGates(unittest.TestCase):
    def test_registry_v2_fields(self):
        reg = load_registry()
        self.assertGreaterEqual(reg.get("version"), 2)
        self.assertIn("sonnet_output_usd_per_mtok", reg["pricing"])
        for repo in reg["repos"]:
            self.assertGreaterEqual(repo.get("clone_depth", 0), 50, "git_diff scenario needs history")
            self.assertIn("pattern", repo.get("raw_search", {}))

    def test_raw_search_gates(self):
        gates = _RRB.raw_search_gates(RAW_SEARCH_SAMPLE, RAW_SEARCH_SAMPLE.splitlines()[0][:120], 40)
        self.assertTrue(gates["matches_exist"])
        self.assertTrue(gates["paths_preserved"])
        gates_small = _RRB.raw_search_gates(RAW_SEARCH_SAMPLE.splitlines()[0], RAW_SEARCH_SAMPLE.splitlines()[0], 3)
        self.assertFalse(gates_small["matches_exist"])

    def test_git_diff_gates_lockfile_exempt(self):
        gates = _RRB.git_diff_gates(RAW_DIFF_SAMPLE, "diff --git a/app.py b/app.py\n+++ b/app.py")
        self.assertTrue(gates["changed_files_preserved"])
        gates_broken = _RRB.git_diff_gates(RAW_DIFF_SAMPLE, "diff --git a/other.py b/other.py")
        self.assertFalse(gates_broken["changed_files_preserved"])

    def test_write_egress_axis_is_output_priced(self):
        bench = RealRepoBenchmark()
        self.assertEqual(bench.pricing.get("sonnet_output_usd_per_mtok"), 15.0)


class TestFailureNoiseStrip(unittest.TestCase):
    RAW_VERBOSE_FAIL = (
        "============================= test session starts ==============================\n"
        "collected 21 items\n"
        "\n"
        "tests/test_a.py::test_alpha PASSED [  4%]\n"
        "tests/test_a.py::test_beta PASSED [  9%]\n"
        "tests/test_a.py::test_gamma PASSED [ 14%]\n"
        "........................................................................ [ 60%]\n"
        "\n"
        "=================================== FAILURES ===================================\n"
        "________________________________ test_bench_injected_regression ________________\n"
        "tests/test_bench_injected.py:2: in test_bench_injected_regression\n"
        "    assert False, \"bench: pre-registered injected regression\"\n"
        "E   AssertionError: bench: pre-registered injected regression\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_bench_injected.py::test_bench_injected_regression - AssertionError\n"
    )

    def test_failure_noise_stripped_but_failures_intact(self):
        from src.command_sanitizer import CommandSanitizer
        compacted = CommandSanitizer.compact_test_output(self.RAW_VERBOSE_FAIL, 1)
        gates = test_output_gates(compacted)
        self.assertTrue(all(gates.values()), "injected test name / error / frame must survive")
        self.assertNotIn("PASSED [", compacted)
        self.assertIn("passed test lines collapsed", compacted)
        self.assertIn("E   AssertionError", compacted, "failure frame must stay intact")
        self.assertLess(len(compacted), len(self.RAW_VERBOSE_FAIL))

    def test_dots_only_lines_dropped_mixed_lines_kept(self):
        from src.command_sanitizer import CommandSanitizer
        compacted = CommandSanitizer.compact_test_output("..... [ 53%]\n....F... [ 99%]\n", 1)
        self.assertIn("....F...", compacted, "lines marking a failure position must stay")
        self.assertNotIn("..... [ 53%]", compacted)


class TestRunLocal(unittest.TestCase):
    def test_run_local_on_tmp_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = os.path.join(tmp, "pkg")
            tests_dir = os.path.join(tmp, "tests")
            os.makedirs(src_dir)
            os.makedirs(tests_dir)
            with open(os.path.join(src_dir, "core.py"), "w") as f:
                f.write(FAT_SOURCE)
            with open(os.path.join(src_dir, "other.py"), "w") as f:
                f.write("def side():\n" + "".join(f"    x{j} = {j}\n" for j in range(40)) + "    return x39\n")
            with open(os.path.join(tests_dir, "test_ok.py"), "w") as f:
                f.write("def test_ok():\n    assert True\n")
            bench = RealRepoBenchmark()
            data = bench.run_local(tmp)
        entry = data["results"][0]
        self.assertIn(entry["status"], ("ok", "partial"), entry.get("reason"))
        self.assertEqual(entry["symbol_read"]["status"], "ok")
        self.assertGreater(entry["symbol_read"]["reduction_pct"], 30.0)
        self.assertTrue(all(entry["symbol_read"]["gates"].values()))
        # test_output: ok (pytest available) or skipped (no pytest in env) — never counted dishonestly
        self.assertIn(entry["test_output"]["status"], ("ok", "gate_failed", "skipped"))


if __name__ == "__main__":
    unittest.main()


class TestGitDiffGateChangedPaths(unittest.TestCase):
    """QA sweep finding: the gate sliced '+++ b/' as [4:] leaving a 'b/' prefix,
    so paths never matched DiffSlicer omission notes ('...asset '<path>'') and
    asset-only HEAD~1 commits failed changed_files_preserved."""

    def test_gate_accepts_omission_note_path(self):
        raw = (
            "diff --git a/docs/assets/logo.svg b/docs/assets/logo.svg\n"
            "--- a/docs/assets/logo.svg\n"
            "+++ b/docs/assets/logo.svg\n"
            "+<svg/>\n"
        )
        sliced = "[Diff Slicer: Omitted diff for lockfile/binary asset 'docs/assets/logo.svg']"
        gates = RealRepoBenchmark.git_diff_gates(raw, sliced)
        self.assertTrue(gates["changed_files_preserved"])

    def test_changed_paths_strips_b_prefix(self):
        raw = "+++ b/src/app.py\n+++ /dev/null\n"
        self.assertEqual(RealRepoBenchmark._changed_paths(raw), ["src/app.py"])  # +++ /dev/null is not a "+++ b/" path

    def test_gate_still_fails_when_path_truly_lost(self):
        raw = "+++ b/src/kept.py\n+++ b/docs/lost.svg\n"
        sliced = "diff --git a/src/kept.py b/src/kept.py\n+++ b/src/kept.py\n"  # lost.svg absent entirely
        gates = RealRepoBenchmark.git_diff_gates(raw, sliced)
        self.assertFalse(gates["changed_files_preserved"])


class TestKVCacheStabilityGate(unittest.TestCase):
    """v5 (QA sweep finding): the raw >=80% hit-rate threshold measured hub-file
    SIZE distribution, not normalizer quality — uptime-kuma's ceiling was 77.9%
    with a perfectly byte-stable normalizer (achieved prefix == theoretical
    append-only ceiling). v5 gates achieved-vs-ceiling instead."""

    def test_registry_v5_renames_stability_gate(self):
        bench = RealRepoBenchmark()
        self.assertEqual(bench.reg.get("version"), 5)
        kv_gates = bench.reg["zero_loss_gates"]["kv_cache"]
        self.assertIn("prefix_stability_ge_95", kv_gates)
        self.assertNotIn("prefix_hit_rate_ge_80", kv_gates)
        self.assertIn("v5 renames the kv_cache gate", bench.reg.get("methodology", ""))

    def test_dominant_last_file_has_low_ceiling(self):
        # append-only ceiling = sum(T[i-1]) / sum(T[i]); when the last hub file
        # dominates, the ceiling drops below any fixed hit-rate threshold
        totals = [100, 110, 120, 130, 140, 150, 160, 170, 180, 900]
        ceiling = sum(totals[:-1]) / sum(totals)
        self.assertLess(ceiling, 0.80)  # v4 gate unreachable with perfect stability
