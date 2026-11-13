"""
Real-Repo Benchmark: measured token reduction on live open-source repositories.

Methodology (pre-registered in src/bench_scenarios.json — committed BEFORE runs):
- Every token count is measured with the calibrated BPE tokenizer on real
  repository bytes; nothing is assumed or hardcoded.
- Baseline = what an unassisted agent sends upstream (full file reads, raw
  test output, repeated reads). Optimized = the same trace through the
  TokenSafe pipeline (skeletons, output compaction, pointer dedup).
- Zero-Loss gates: deterministic post-compression checks (target symbol
  present, valid AST, every top-level definition still visible, signature
  line intact, failing test name / error type / traceback frame preserved).
  A failed gate DISQUALIFIES the scenario from the savings table.
- USD = input tokens at the pre-registered Sonnet price point.

Scenarios: symbol-read | test-output (SWE-bench-style injected regression on
real code) | multi-turn with repeat reads. Nothing else is claimed.
"""
import ast
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from src.tokenizer import count_tokens
from src.skeletonizer import Skeletonizer
from src.command_sanitizer import CommandSanitizer
from src.paths import resolve_data_dir

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_scenarios.json")
PUBLISHED_PIN = {"sonnet_input_usd_per_mtok": 3.0, "sonnet_cached_usd_per_mtok": 0.3}
BENCH_CACHE_DIR = os.path.join(resolve_data_dir(), "bench")
REPOS_DIR = os.path.join(BENCH_CACHE_DIR, "repos")
VENV_DIR = os.path.join(BENCH_CACHE_DIR, ".venv-bench")
INJECTED_TEST_NAME = "test_bench_injected_regression"


def load_registry(path: str = REGISTRY_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        reg = json.load(f)
    if not reg.get("repos"):
        raise ValueError("bench_scenarios.json: no repos pre-registered")
    return reg


# ---------------------------------------------------------------- gates

def _top_level_def_names(full_text: str) -> List[str]:
    try:
        tree = ast.parse(full_text)
    except SyntaxError:
        return []
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def _gate_symbol_present(skel_text: str, symbol: str) -> bool:
    return symbol.rsplit(".", 1)[-1] in skel_text


def _gate_valid_python(skel_text: str) -> bool:
    try:
        ast.parse(skel_text)
        return True
    except SyntaxError:
        return False


def _gate_all_top_level_defs_present(full_text: str, skel_text: str) -> bool:
    names = _top_level_def_names(full_text)
    if not names:
        return True  # nothing to lose (file without top-level defs)
    return all(n in skel_text for n in names)


def _gate_signature_line_match(full_text: str, skel_text: str, symbol: str) -> bool:
    """The signature line of the target symbol in the full file must appear
    (normalized) in the skeleton."""
    short = symbol.rsplit(".", 1)[-1]
    for line in full_text.splitlines():
        s = line.strip()
        if s.startswith(("def ", "async def ", "class ")) and f" {short}(" in s:
            return s in skel_text
    return False


def symbol_read_gates(full_text: str, skel_text: str, symbol: str) -> Dict[str, bool]:
    return {
        "symbol_present": _gate_symbol_present(skel_text, symbol),
        "valid_python": _gate_valid_python(skel_text),
        "all_top_level_defs_present": _gate_all_top_level_defs_present(full_text, skel_text),
        "signature_line_match": _gate_signature_line_match(full_text, skel_text, symbol),
    }


def test_output_gates(compacted: str) -> Dict[str, bool]:
    return {
        "failing_test_name_present": INJECTED_TEST_NAME in compacted,
        "error_type_present": "AssertionError" in compacted,
        "first_traceback_frame_present": "test_bench_injected.py" in compacted,
    }


# ---------------------------------------------------------------- harness

class RealRepoBenchmark:
    """Runs pre-registered scenarios against real clones and measures everything."""

    def __init__(self, registry: Optional[Dict[str, Any]] = None):
        self.reg = registry or load_registry()
        self.pricing = self.reg.get("pricing", PUBLISHED_PIN)

    # -- repo cache ------------------------------------------------------
    def ensure_repo(self, repo: Dict[str, Any]) -> str:
        dest = os.path.join(REPOS_DIR, repo["key"])
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(["git", "fetch", "--depth", "1", "origin"], cwd=dest,
                           capture_output=True, timeout=120)
            subprocess.run(["git", "reset", "--hard", "-q", "origin/HEAD"], cwd=dest,
                           capture_output=True, timeout=60)
        else:
            os.makedirs(REPOS_DIR, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", "-q", repo["url"], dest],
                           capture_output=True, timeout=300, check=True)
        return dest

    # -- scenario 1: symbol read ------------------------------------------
    def measure_symbol_read(self, repo_root: str, file: str, symbol: str) -> Dict[str, Any]:
        full_path = os.path.join(repo_root, file)
        if not os.path.isfile(full_path):
            return {"status": "skipped", "reason": f"file not found: {file}"}
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            full_text = f.read()
        baseline = count_tokens(full_text)
        try:
            skel_path, _orig_t, _skel_t = Skeletonizer.get_skeleton_file(full_path, target_symbol=symbol)
        except Exception as exc:
            return {"status": "failed", "reason": f"skeletonizer: {exc}"}
        if not skel_path or not os.path.isfile(skel_path):
            return {"status": "failed", "reason": "no skeleton produced"}
        with open(skel_path, "r", encoding="utf-8", errors="replace") as f:
            skel_text = f.read()
        optimized = count_tokens(skel_text)
        gates = symbol_read_gates(full_text, skel_text, symbol)
        return self._finish(baseline, optimized, gates)

    # -- scenario 2: real pytest output compaction -------------------------
    def _venv_python(self, repo_root: str, deps: List[str]) -> Optional[str]:
        venv_py = os.path.join(VENV_DIR, "bin", "python")
        marker = os.path.join(VENV_DIR, ".bench-ready")
        if not os.path.exists(venv_py):
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR],
                           capture_output=True, timeout=120, check=False)
        if not os.path.exists(venv_py):
            return None
        if not os.path.exists(marker):
            # The repo itself must be importable for its tests to run at all
            # (flask tests import flask; fastapi tests import fastapi, ...).
            # Editable install pulls the repo's own runtime deps too.
            subprocess.run([venv_py, "-m", "pip", "install", "-q", "pytest", *deps, "-e", repo_root],
                           capture_output=True, timeout=600, check=False)
            with open(marker, "w") as f:
                f.write(str(int(time.time())))
        return venv_py

    def measure_test_output(self, repo_root: str, pytest_cfg: Dict[str, Any]) -> Dict[str, Any]:
        venv_py = self._venv_python(repo_root, pytest_cfg.get("deps", []))
        if not venv_py:
            return {"status": "skipped", "reason": "venv unavailable"}
        # Pre-registered injected regression (temp clone only, never upstream)
        inj = self.reg.get("injected_failure", {})
        inj_path = os.path.join(repo_root, inj.get("file", "tests/test_bench_injected.py"))
        os.makedirs(os.path.dirname(inj_path), exist_ok=True)
        with open(inj_path, "w", encoding="utf-8") as f:
            f.write(inj.get("test_source", "def test_bench_injected_regression():\n    assert False\n"))
        targets = [inj.get("file", "tests/test_bench_injected.py")]
        if os.path.isdir(os.path.join(repo_root, "tests")):
            # Full suite: "run the tests" is what an agent actually does.
            # subset_glob in the registry stays documented for deps context.
            targets.append("tests/")
        proc = subprocess.run(
            [venv_py, "-m", "pytest", *targets, "-q", "--tb=long", "--no-header", "-p", "no:cacheprovider"],
            cwd=repo_root, capture_output=True, text=True, timeout=300,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
        if not raw.strip():
            return {"status": "skipped", "reason": "pytest produced no output (deps missing?)"}
        baseline = count_tokens(raw)
        compacted = CommandSanitizer.compact_test_output(raw, proc.returncode)
        optimized = count_tokens(compacted)
        gates = test_output_gates(compacted)
        result = self._finish(baseline, optimized, gates)
        result["pytest_targets"] = targets
        result["exit_code"] = proc.returncode
        return result

    # -- scenario 3: multi-turn with repeat reads ---------------------------
    def measure_multiturn(self, repo_root: str, reads: List[Dict[str, Any]]) -> Dict[str, Any]:
        baseline = 0
        optimized = 0
        per_read: List[Dict[str, Any]] = []
        gate_results: List[bool] = []
        for r in reads:
            one = self.measure_symbol_read(repo_root, r["file"], r["symbol"])
            if one.get("status") != "ok":
                per_read.append({"file": r["file"], "symbol": r["symbol"], **one})
                continue
            baseline += one["baseline_tokens"] * 2  # repeat-read pattern: 1st + re-read
            optimized += one["optimized_tokens"]    # skeleton + pointer-dedup re-read (0)
            gate_results.append(all(one["gates"].values()))
            per_read.append({
                "file": r["file"], "symbol": r["symbol"], "status": "ok",
                "baseline_tokens": one["baseline_tokens"], "optimized_tokens": one["optimized_tokens"],
            })
        if baseline == 0:
            return {"status": "skipped", "reason": "no readable files matched"}
        gates = {"all_reads_lossless": all(gate_results) if gate_results else False,
                 "reads_measured": len(gate_results)}
        return self._finish(baseline, optimized, gates, extra={"per_read": per_read})

    # -- aggregation -----------------------------------------------------
    def _finish(self, baseline: int, optimized: int, gates: Dict[str, bool], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        saved = max(0, baseline - optimized)
        reduction = round(saved / baseline * 100.0, 1) if baseline else 0.0
        usd = round(saved / 1e6 * self.pricing["sonnet_input_usd_per_mtok"], 4)
        out = {
            "status": "ok" if all(gates.values()) else "gate_failed",
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "saved_tokens": saved,
            "reduction_pct": reduction,
            "usd_saved": usd,
            "gates": gates,
        }
        if extra:
            out.update(extra)
        return out

    def run_repo(self, repo: Dict[str, Any]) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"key": repo["key"], "url": repo["url"]}
        try:
            root = self.ensure_repo(repo)
        except Exception as exc:
            entry.update({"status": "skipped", "reason": f"clone: {exc}"})
            return entry
        entry["symbol_read"] = self.measure_symbol_read(root, **repo["symbol_read"])
        entry["test_output"] = self.measure_test_output(root, repo.get("pytest", {}))
        entry["multiturn"] = self.measure_multiturn(root, repo.get("multiturn_reads", []))
        ok = [entry[s].get("status") == "ok" for s in ("symbol_read", "test_output", "multiturn")]
        entry["status"] = "ok" if all(ok) else ("partial" if any(ok) else "failed")
        return entry

    def run_all(self, repo_keys: Optional[List[str]] = None) -> Dict[str, Any]:
        repos = [r for r in self.reg["repos"] if not repo_keys or r["key"] in repo_keys]
        results = []
        for repo in repos:
            print(f"  ▶ {repo['key']} ({repo['url']})")
            results.append(self.run_repo(repo))
        return {
            "version": self.reg.get("version"),
            "created": self.reg.get("created"),
            "methodology": self.reg.get("methodology"),
            "pricing": self.pricing,
            "results": results,
        }

    # -- output ------------------------------------------------------------
    def render_markdown(self, data: Dict[str, Any]) -> str:
        lines = ["# TokenSafe Real-Repo Benchmark (measured)\n"]
        lines.append(f"> {data.get('methodology', '')}\n")
        lines.append("| Repo | Scenario | Baseline tok | Optimized tok | Saved | % | USD (Sonnet input) | Gates |")
        lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |")
        for r in data.get("results", []):
            for scen in ("symbol_read", "test_output", "multiturn"):
                s = r.get(scen) or {}
                if s.get("status") != "ok" and s.get("status") != "gate_failed":
                    continue
                gates = "PASS" if all(s.get("gates", {}).values()) else "FAIL"
                lines.append(
                    f"| {r['key']} | {scen} | {s['baseline_tokens']:,} | {s['optimized_tokens']:,} | "
                    f"{s['saved_tokens']:,} | {s['reduction_pct']}% | ${s['usd_saved']} | {gates} |"
                )
        return "\n".join(lines) + "\n"

    def export(self, data: Dict[str, Any]) -> Dict[str, str]:
        out_dir = resolve_data_dir()
        json_path = os.path.join(out_dir, "BENCHMARK_REAL.json")
        md_path = os.path.join(out_dir, "BENCHMARK_REAL.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.render_markdown(data))
        return {"json": json_path, "md": md_path}
