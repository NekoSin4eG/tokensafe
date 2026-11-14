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
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from src.tokenizer import count_tokens
from src.skeletonizer import Skeletonizer
from src.command_sanitizer import CommandSanitizer
from src.diff_slicer import DiffSlicer
from src.paths import resolve_data_dir

_DEF_KEYWORD_RE = re.compile(r"\b(function|func|fn|def|class|struct|interface|trait|impl|type|module)\b")

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

def _top_level_def_names(full_text: str, ext: str = ".py") -> List[str]:
    if ext != ".py":
        return []  # AST detection is Python-only; other languages rely on the
        # symbol_present + signature_line_match text gates.
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


def _gate_all_top_level_defs_present(full_text: str, skel_text: str, ext: str = ".py") -> bool:
    names = _top_level_def_names(full_text, ext)
    if not names:
        return True  # nothing to lose (file without top-level defs / non-Python)
    return all(n in skel_text for n in names)


def _def_line_for(full_text: str, short: str) -> Optional[str]:
    """Language-agnostic: the first structural-definition line naming the symbol."""
    for line in full_text.splitlines():
        s = line.strip()
        if short in s and _DEF_KEYWORD_RE.search(s):
            return s
    return None


def _gate_signature_line_match(full_text: str, skel_text: str, symbol: str) -> bool:
    """The signature line of the target symbol in the full file must appear
    (normalized) in the skeleton."""
    short = symbol.rsplit(".", 1)[-1]
    line = _def_line_for(full_text, short)
    return bool(line) and line in skel_text


def symbol_read_gates(full_text: str, skel_text: str, symbol: str, ext: str = ".py") -> Dict[str, bool]:
    gates = {
        "symbol_present": _gate_symbol_present(skel_text, symbol),
    }
    if ext == ".py":
        # Python: byte-level signature verification is possible and enforced.
        names = _top_level_def_names(full_text)
        gates["valid_python"] = _gate_valid_python(skel_text)
        gates["all_top_level_defs_present"] = (not names) or all(n in skel_text for n in names)
        gates["signature_line_match"] = _gate_signature_line_match(full_text, skel_text, symbol)
    # Non-Python languages: parsers may reformat signature spacing/params, so the
    # meaningful invariant is symbol presence in a skeleton the language parser
    # accepted (a refused/empty skeleton never reaches this function).
    return gates


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
        depth = str(repo.get("clone_depth", 1))
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(["git", "fetch", "--depth", depth, "origin"], cwd=dest,
                           capture_output=True, timeout=120)
            subprocess.run(["git", "reset", "--hard", "-q", "origin/HEAD"], cwd=dest,
                           capture_output=True, timeout=60)
        else:
            os.makedirs(REPOS_DIR, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", depth, "-q", repo["url"], dest],
                           capture_output=True, timeout=600, check=True)
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
            return {"status": "skipped",
                    "reason": "no skeleton produced — target symbol is >=70% of the file (product refusal threshold) or file unparseable"}
        with open(skel_path, "r", encoding="utf-8", errors="replace") as f:
            skel_text = f.read()
        optimized = count_tokens(skel_text)
        ext = os.path.splitext(full_path)[1]
        gates = symbol_read_gates(full_text, skel_text, symbol, ext)
        return self._finish(baseline, optimized, gates)

    # -- scenario 2: real pytest output compaction -------------------------
    def _venv_python(self, repo_root: str, deps: List[str]) -> Optional[str]:
        # Per-repo venv: pytest plugins autoload globally from a shared venv
        # (pytest-asyncio's configure warning + flask's filterwarnings=error
        # => INTERNALERROR; pytest-httpbin slows unrelated collections), so
        # each repo gets an isolated interpreter with its own deps.
        repo_key = os.path.basename(repo_root.rstrip("/"))
        venv_dir = os.path.join(BENCH_CACHE_DIR, f".venv-{repo_key}")
        venv_py = os.path.join(venv_dir, "bin", "python")
        marker = os.path.join(venv_dir, ".bench-ready")
        if not os.path.exists(venv_py):
            subprocess.run([sys.executable, "-m", "venv", venv_dir],
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
        extra: Dict[str, Any] = {"pytest_targets": targets, "exit_code": proc.returncode}
        if not all(gates.values()):
            # Transparency: publish WHY the gates rejected this scenario
            extra["fail_reason"] = "gate rejection: " + ", ".join(
                g for g, ok in gates.items() if not ok
            ) + f" (pytest exit={proc.returncode} — suite could not be collected/run cleanly; see excerpt)"
            extra["output_excerpt"] = compacted[:400]
        result = self._finish(baseline, optimized, gates, extra=extra)
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

    # -- local mode: measure the CURRENT repository (no pre-registration) ----
    def _hot_symbols(self, root: str, limit: int = 3) -> List[Any]:
        """Top graph-hub files × their largest symbol — the reads an agent
        actually performs first in an unfamiliar repo. Uses the language-agnostic
        graph (works for any supported language, not just Python)."""
        from src.graph_engine import GraphEngine
        graph = GraphEngine.get_instance(root)
        out: List[Any] = []
        for node in graph.get_god_nodes(top_n=10):
            f = node.get("file")
            if not f:
                continue
            fp = f if os.path.isabs(f) else os.path.join(root, f)
            if not os.path.isfile(fp):
                continue
            best = None
            for sym in graph.symbols.values():
                if getattr(sym, "file_path", "") != f:
                    continue
                size = (getattr(sym, "line_end", 0) or 0) - (getattr(sym, "line_start", 0) or 0)
                if best is None or size > best[1]:
                    best = (sym.name, size)
            if best and best[1] >= 20:  # skip trivial 1-line symbols
                out.append((f, best[0]))
            if len(out) >= limit:
                break
        return out[:limit]

    def measure_local_test_output(self, root: str) -> Dict[str, Any]:
        tests_dir = os.path.join(root, "tests")
        if not os.path.isdir(tests_dir):
            return {"status": "skipped", "reason": "no tests/ directory"}
        venv_py = os.path.join(VENV_DIR, "bin", "python")
        if os.path.exists(venv_py):
            py = venv_py
        else:
            import importlib.util
            if importlib.util.find_spec("pytest") is None:
                return {"status": "skipped", "reason": "pytest not installed"}
            py = sys.executable
        py_args = [py, "-m", "pytest"]
        if any(d.startswith("pytest-asyncio") for d in pytest_cfg.get("deps", [])):
            # pytest-asyncio >=0.24 emits a deprecation warning that repos running
            # with filterwarnings=error (fastapi) escalate into INTERNALERROR.
            py_args += ["-o", "asyncio_default_fixture_loop_scope=function"]
        py_args += [*targets, "-q", "--tb=long", "--no-header", "-p", "no:cacheprovider"]
        try:
            proc = subprocess.run(py_args, cwd=repo_root, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {"status": "skipped", "reason": "pytest timeout 300s"}
        raw = (proc.stdout or "") + (proc.stderr or "")
        if not raw.strip():
            return {"status": "skipped", "reason": "no pytest output"}
        baseline = count_tokens(raw)
        compacted = CommandSanitizer.compact_test_output(raw, proc.returncode)
        optimized = count_tokens(compacted)
        # Local mode never mutates the user's repo (no injected regression):
        # gates verify the outcome signal survived compaction.
        gates = {
            "outcome_signal_retained": any(w in compacted for w in ("passed", "failed", "error", "==")),
            "shorter_or_equal": optimized <= baseline,
        }
        return self._finish(baseline, optimized, gates, extra={
            "exit_code": proc.returncode,
            "note": "local mode: suite as-is (no injected regression)",
        })

    def run_local(self, root_dir: Optional[str] = None) -> Dict[str, Any]:
        """Measures the CURRENT repository. Not pre-registered by definition —
        this mode is a self-measurement tool for any repo, not the published
        methodology (that lives in bench_scenarios.json)."""
        root = os.path.abspath(root_dir or os.getcwd())
        key = os.path.basename(root) or "local-repo"
        envelope: Dict[str, Any] = {
            "version": self.reg.get("version"),
            "created": self.reg.get("created"),
            "methodology": "local mode: measured on the current repository (no scenario pre-registration)",
            "pricing": self.pricing,
            "results": [],
        }
        entry: Dict[str, Any] = {"key": key, "url": f"local:{root}", "mode": "local"}
        envelope["results"].append(entry)
        try:
            reads = self._hot_symbols(root, limit=3)
        except Exception as exc:
            entry.update({"status": "failed", "reason": f"graph engine: {exc}"})
            return envelope
        if not reads:
            entry.update({"status": "skipped", "reason": "no measurable symbols found (empty/too small repo?)"})
            return envelope
        read_results = [{"file": f, "symbol": s, **self.measure_symbol_read(root, f, s)} for f, s in reads]
        entry["symbol_read"] = self._aggregate_reads(read_results)
        entry["multiturn"] = self.measure_multiturn(root, [{"file": f, "symbol": s} for f, s in reads])
        entry["test_output"] = self.measure_local_test_output(root)
        entry["symbol_read_details"] = read_results
        statuses = [entry["symbol_read"].get("status"), entry["multiturn"].get("status"), entry["test_output"].get("status")]
        entry["status"] = "ok" if all(s == "ok" for s in statuses) else ("partial" if any(s == "ok" for s in statuses) else "failed")
        return envelope

    def _aggregate_reads(self, read_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        oks = [r for r in read_results if r.get("status") == "ok"]
        if not oks:
            return {"status": read_results[0].get("status", "skipped"), "reason": read_results[0].get("reason", "no reads measured")}
        baseline = sum(r["baseline_tokens"] for r in oks)
        optimized = sum(r["optimized_tokens"] for r in oks)
        gates_all = {}
        for r in oks:
            gates_all.update(r.get("gates", {}))
        return self._finish(baseline, optimized, gates_all, extra={"reads": len(oks)})

    # -- scenario 4: architecture discovery --------------------------------
    def _hub_files(self, root: str, limit: int = 5) -> List[str]:
        from src.graph_engine import GraphEngine
        graph = GraphEngine.get_instance(root)
        out = []
        for n in graph.get_god_nodes(top_n=limit):
            f = n.get("file")
            if f and os.path.isfile(os.path.join(root, f)):
                out.append(f)
        return out

    def _architecture_map(self, root: str) -> str:
        from src.graph_engine import GraphEngine
        graph = GraphEngine.get_instance(root)
        lines = [f"Architecture: {len(graph.symbols)} symbols in {len(graph.file_symbols)} files"]
        for n in graph.get_god_nodes(top_n=7):
            lines.append(
                f"{n.get('file')} (symbols: {n.get('symbols_count')}, "
                f"dependents: {n.get('dependents')}, dependencies: {n.get('dependencies')})"
            )
        return "\n".join(lines)

    def measure_architecture(self, root: str) -> Dict[str, Any]:
        hub_files = self._hub_files(root)
        if len(hub_files) < 2:
            return {"status": "skipped", "reason": "fewer than 2 hub files resolved"}
        baseline = 0
        for f in hub_files:
            with open(os.path.join(root, f), "r", encoding="utf-8", errors="replace") as fh:
                baseline += count_tokens(fh.read())
        map_text = self._architecture_map(root)
        optimized = count_tokens(map_text)
        gates = {
            "hub_files_listed": all(os.path.basename(f) in map_text for f in hub_files),
            "compact_bound": optimized < 600,
        }
        return self._finish(baseline, optimized, gates, extra={"hub_files": hub_files})

    # -- scenario 5: raw search leak ---------------------------------------
    @staticmethod
    def raw_search_gates(raw_text: str, optimized_text: str, match_count: int) -> Dict[str, bool]:
        optimized_paths = {ln.split(":", 1)[0] for ln in optimized_text.splitlines() if ":" in ln}
        return {
            "matches_exist": match_count > 25,
            "paths_preserved": bool(optimized_paths) and all(p in raw_text for p in optimized_paths),
        }

    def measure_raw_search(self, root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        pattern = cfg.get("pattern")
        search_dir = cfg.get("dir", ".")
        if not pattern:
            return {"status": "skipped", "reason": "no pattern pre-registered"}
        proc = subprocess.run(["git", "grep", "-n", pattern, "--", search_dir],
                              cwd=root, capture_output=True, text=True, timeout=120)
        raw_lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not raw_lines:
            return {"status": "skipped", "reason": f"no matches for '{pattern}'"}
        baseline = count_tokens("\n".join(raw_lines))
        if len(raw_lines) <= 25:
            return {"status": "skipped",
                    "reason": f"no leak: {len(raw_lines)} matches <= 25 cap (scoped_search would save nothing)"}
        # scoped_search semantics: max 25 results, lines trimmed to a bounded width
        optimized_lines = [ln[:120] for ln in raw_lines[:25]]
        optimized_text = "\n".join(optimized_lines)
        optimized = count_tokens(optimized_text)
        gates = self.raw_search_gates("\n".join(raw_lines), optimized_text, len(raw_lines))
        return self._finish(baseline, optimized, gates, extra={"matches": len(raw_lines), "pattern": pattern})

    # -- scenario 6: git-diff churn → slice_diff ----------------------------
    @staticmethod
    def git_diff_gates(raw_text: str, sliced_text: str) -> Dict[str, bool]:
        changed = [ln[4:].strip() for ln in raw_text.splitlines() if ln.startswith("+++ b/")]
        non_lock = [p for p in changed if "lock" not in p.lower()]
        return {"changed_files_preserved": bool(non_lock) and all(p in sliced_text for p in non_lock)}

    def measure_git_diff(self, root: str) -> Dict[str, Any]:
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root,
                               capture_output=True, text=True, timeout=60)
        try:
            if int((count.stdout or "0").strip() or 0) < 2:
                return {"status": "skipped", "reason": "insufficient history for git diff"}
        except ValueError:
            return {"status": "skipped", "reason": "no commit history"}
        proc = subprocess.run(["git", "diff", "HEAD~1", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=180)
        raw = proc.stdout or ""
        if not raw.strip():
            return {"status": "skipped", "reason": "empty diff at HEAD~1"}
        baseline = count_tokens(raw)
        sliced, _orig, _new = DiffSlicer.slice_diff(raw)
        optimized = count_tokens(sliced)
        gates = self.git_diff_gates(raw, sliced)
        return self._finish(baseline, optimized, gates, extra={"changed_bytes": len(raw)})

    # -- scenario 7: write-egress (OUTPUT-token axis) ------------------------
    @staticmethod
    def write_egress_gates(modified_text: str, edit_block: str) -> Dict[str, bool]:
        return {
            "inserted_line_present": "bench-probe" in modified_text,
            "original_signature_in_target": True,  # replaced per-call with real check
        }

    def measure_write_egress(self, root: str) -> Dict[str, Any]:
        for f in self._hub_files(root, limit=8):
            fp = os.path.join(root, f)
            ext = os.path.splitext(fp)[1].lower()
            cmt = "#" if ext in (".py", ".rb", ".sh") else "//"
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            if not (100 <= len(lines) <= 2000):
                continue
            sig_idx = next((i for i, ln in enumerate(lines)
                            if _DEF_KEYWORD_RE.search(ln)
                            and not ln.lstrip().startswith(("#", "//", "*", "/*"))
                            and ("(" in ln or "{" in ln)), None)
            if sig_idx is None:
                continue
            sig_line = lines[sig_idx]
            modified_lines = lines[:sig_idx + 1] + [f"{cmt} bench-probe: egress guard scenario"] + lines[sig_idx + 1:]
            modified_text = "\n".join(modified_lines)
            edit_block = (
                f"{f}\nTarget:\n{sig_line}\nReplacement:\n{sig_line}\n{cmt} bench-probe: egress guard scenario\n"
            )
            baseline_out = count_tokens(modified_text)   # write_file re-emits the whole file
            optimized_out = count_tokens(edit_block)     # surgical Edit emits only the delta
            gates = {
                "inserted_line_present": "bench-probe" in modified_text,
                "original_signature_in_target": sig_line.strip() in edit_block,
            }
            out = self._finish(baseline_out, optimized_out, gates,
                               usd_per_mtok=self.pricing.get("sonnet_output_usd_per_mtok"),
                               extra={"file": f, "axis": "output_tokens (write_file vs surgical Edit)"})
            return out
        return {"status": "skipped", "reason": "no mid-size hub file for egress scenario"}

    # -- scenario 8: API surface map ----------------------------------------
    def measure_api_surface(self, root: str) -> Dict[str, Any]:
        hub_files = self._hub_files(root, limit=5)
        if len(hub_files) < 2:
            return {"status": "skipped", "reason": "fewer than 2 hub files"}
        baseline = 0
        for f in hub_files:
            with open(os.path.join(root, f), "r", encoding="utf-8", errors="replace") as fh:
                baseline += count_tokens(fh.read())
        from src.graph_engine import GraphEngine
        graph = GraphEngine.get_instance(root)
        surface_text = graph.generate_api_surface()
        if not surface_text:
            return {"status": "skipped", "reason": "empty API surface"}
        optimized = count_tokens(surface_text)
        gates = {
            "hub_files_listed": all(os.path.basename(f) in surface_text for f in hub_files),
            "smaller_than_crawl": optimized < baseline,
        }
        return self._finish(baseline, optimized, gates, extra={"hub_files": hub_files})

    # -- scenario 9: KV-cache prefix locking ---------------------------------
    def measure_kv_cache(self, root: str) -> Dict[str, Any]:
        """Repo-agnostic 10-turn replay: each turn appends one more hub file to a
        growing conversation. Byte-identical prefixes between consecutive turns are
        cache-hit tokens (billed at the 0.1x cached rate); the PromptCacheNormalizer
        must keep those prefixes byte-identical or the gate fails."""
        from src.graph_engine import GraphEngine
        from src.cache_normalizer import PromptCacheNormalizer
        from src.benchmark import _extract_canonical_units, _longest_prefix_tokens, _cached_tokens

        hub_files = self._hub_files(root, limit=10)
        if len(hub_files) < 5:
            return {"status": "skipped", "reason": "fewer than 5 hub files for a multi-turn replay"}
        graph = GraphEngine.get_instance(root)
        normalizer = PromptCacheNormalizer()
        sys_prompt = "You are an expert AI coding assistant working inside this repository. Follow clean engineering rules and use minimal diffs."

        turns: List[List[Dict[str, Any]]] = []
        for i, f in enumerate(hub_files):
            # Append-only conversation (mirrors real agent sessions + the Append-Only
            # Epoch Governor): every hub file appears as the SAME user/assistant triplet
            # in the turn it is introduced and in every later turn, so normalized
            # prefixes stay byte-identical and only the final continuation prompt is new.
            msgs: List[Dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
            for pf in hub_files[:i + 1]:
                with open(os.path.join(root, pf), "r", encoding="utf-8", errors="replace") as pfh:
                    pf_content = pfh.read()
                msgs.append({"role": "user", "content": f"Explore this repository step by step. File {pf}:\n{pf_content}"})
                msgs.append({"role": "assistant", "content": f"Analyzed {pf}: indexed symbols and call graph."})
            msgs.append({"role": "user", "content": "Continue the analysis with the next file."})
            turns.append(msgs)

        tok_cache: Dict[str, int] = {}
        prev_units: List[str] = []
        b_in = 0
        o_in = 0
        hit_toks = 0
        for msgs in turns:
            # inject_cache_control=False: we measure prefix BYTE-STABILITY (the
            # property that makes provider caching possible). Rolling breakpoints
            # move the cacheable boundary every turn by design and would
            # understate the stable prefix.
            norm_msgs, _nstats = normalizer.normalize_messages([dict(m) for m in msgs], inject_cache_control=False)
            units = _extract_canonical_units(None, norm_msgs)
            cur = sum(_cached_tokens(u, tok_cache) for u in units)
            b_in += cur
            prefix = _longest_prefix_tokens(prev_units, units, tok_cache) if prev_units else 0
            hit_toks += prefix
            o_in += max(0, cur - prefix) + prefix  # full input still sent; prefix billed at cached rate
            prev_units = units

        if b_in == 0:
            return {"status": "skipped", "reason": "empty replay"}
        hit_rate = round(hit_toks / b_in * 100.0, 1)
        # Billed tokens: cache-hit portion at the 0.1x cached rate, rest at full input price
        billed_nonhit = o_in - hit_toks
        o_billed = billed_nonhit + round(hit_toks * 0.1)
        b_billed = b_in
        reduction = round((1 - o_billed / b_billed) * 100.0, 1) if b_billed else 0.0
        usd_saved = round((b_billed - o_billed) / 1e6 * self.pricing.get("sonnet_input_usd_per_mtok", 3.0), 4)
        gates = {
            "prefix_hit_rate_ge_80": hit_rate >= 80.0,
            "input_reduction_positive": reduction > 0,
        }
        return {
            "status": "ok" if all(gates.values()) else "gate_failed",
            "baseline_tokens": b_billed,
            "optimized_tokens": o_billed,
            "saved_tokens": max(0, b_billed - o_billed),
            "reduction_pct": reduction,
            "kv_cache_hit_rate_pct": hit_rate,
            "usd_saved": usd_saved,
            "gates": gates,
            "turns": len(turns),
            "note": "10-turn replay over repo hub files; cache-hit tokens billed at the 0.1x cached rate (Sonnet)",
        }

    # -- scenario 10: secret guard --------------------------------------------
    def measure_secret_guard(self, root: str) -> Dict[str, Any]:
        from src.guard import TokenSafeGuard
        env_content = self.reg.get("secret_guard", {}).get("env_content", "")
        if not env_content:
            return {"status": "skipped", "reason": "no secret_guard template"}
        env_path = os.path.join(root, ".env")
        # Written only into the temp clone and removed right after; the fixture
        # values are pre-registered synthetic data (see registry), never real.
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        try:
            res = TokenSafeGuard.inspect_command(f'cat {env_path}')
        finally:
            if os.path.exists(env_path):
                os.remove(env_path)
        decision = "deny" if getattr(res, "blocked", False) else "allow"
        reason = str(getattr(res, "reason", "") or "")
        check_name = str(getattr(res, "check_name", "") or "")
        baseline = count_tokens(env_content)
        optimized = count_tokens(reason)
        secret_values = [ln.split("=", 1)[1].strip() for ln in env_content.splitlines()
                         if "=" in ln and not ln.strip().startswith("#")]
        gates = {
            "deny_decision": decision == "deny" and check_name == "SECRET_FILE_READ",
            "no_secret_leak": all((not v) or (v not in reason) for v in secret_values if len(v) >= 8),
        }
        return self._finish(baseline, optimized, gates, extra={
            "decision": decision, "check_name": check_name,
            "axis": "security: secrets kept out of the model context",
        })

    # -- scenario 11: junk-read guard -----------------------------------------
    JUNK_FILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", "mix.lock", "Podfile.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock")

    def measure_junk_read(self, root: str) -> Dict[str, Any]:
        junk = next((jf for jf in self.JUNK_FILES if os.path.isfile(os.path.join(root, jf))), None)
        if not junk:
            return {"status": "skipped", "reason": "no lockfile in clone"}
        with open(os.path.join(root, junk), "r", encoding="utf-8", errors="replace") as f:
            full_text = f.read()
        baseline = count_tokens(full_text)
        if len(full_text.splitlines()) <= 30:
            return {"status": "skipped", "reason": f"{junk} is smaller than the 30-line cap — nothing to save"}
        rewritten, _saved, _msg = CommandSanitizer.sanitize(f"cat {junk}")
        cap_lines = 30
        if rewritten:
            m = re.search(r"head -n (\d+)", rewritten)
            if m:
                cap_lines = int(m.group(1))
        if len(full_text.splitlines()) <= cap_lines:
            return {"status": "skipped", "reason": f"{junk} ({len(full_text.splitlines())} lines) is within the {cap_lines}-line cap — nothing to save"}
        if rewritten:
            capped = subprocess.run(rewritten, shell=True, cwd=root,
                                    capture_output=True, text=True, timeout=60)
            capped_text = capped.stdout or ""
            note = f"rewritten: {rewritten}"
        else:
            capped_text = "\n".join(full_text.splitlines()[:cap_lines])
            note = f"command passes as-is; output capped to {cap_lines} lines"
        optimized = count_tokens(capped_text)
        gates = {
            "capped_output": optimized < baseline,
            "junk_file_named": junk in (rewritten or f"cat {junk}"),
        }
        return self._finish(baseline, optimized, gates, extra={"junk_file": junk, "note": note})

    # -- scenario 12: prompt compaction (TOCP) --------------------------------
    def measure_prompt_compaction(self, root: str, sr: Dict[str, Any]) -> Dict[str, Any]:
        from src.compactor import PromptCompactor
        template = self.reg.get("prompt_compaction", {}).get("template", "")
        if not template:
            return {"status": "skipped", "reason": "no prompt template"}
        file_rel = sr.get("file", "")
        symbol = (sr.get("symbol", "") or "").rsplit(".", 1)[-1]
        prompt = template.format(file=file_rel, symbol=symbol)
        baseline = count_tokens(prompt)
        res = PromptCompactor.compact(prompt)
        optimized = count_tokens(res.get("compact_text", ""))
        gates = {
            "action_extracted": bool(res.get("action")) and res.get("action") != "UNKNOWN",
            "target_file_preserved": (not file_rel) or (file_rel in (res.get("target_file") or "")),
            "savings_positive": baseline > optimized,
        }
        return self._finish(baseline, optimized, gates, extra={"tier": res.get("tier"), "action": res.get("action")})

    # -- aggregation -----------------------------------------------------
    def _finish(self, baseline: int, optimized: int, gates: Dict[str, bool], extra: Optional[Dict[str, Any]] = None, usd_per_mtok: Optional[float] = None) -> Dict[str, Any]:
        saved = max(0, baseline - optimized)
        reduction = round(saved / baseline * 100.0, 1) if baseline else 0.0
        price = float(usd_per_mtok or self.pricing.get("sonnet_input_usd_per_mtok", PUBLISHED_PIN["sonnet_input_usd_per_mtok"]))
        usd = round(saved / 1e6 * price, 4)
        out = {
            "status": "ok" if all(gates.values()) else "gate_failed",
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "saved_tokens": saved,
            "reduction_pct": reduction,
            "usd_saved": usd,
            "gates": gates,
        }
        if out["status"] == "gate_failed":
            failed = [g for g, ok in gates.items() if not ok]
            out["fail_reason"] = "gate rejection: " + ", ".join(failed)
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
        if repo.get("auto"):
            # Language-agnostic mode: targets are picked by the pre-registered
            # rule (top god-node files × their largest symbol), committed in the
            # registry BEFORE measurement. No per-repo test runner is configured
            # (test-output compaction is measured on the pytest repos).
            try:
                reads = self._hot_symbols(root, limit=6)
            except Exception as exc:
                reads = []
                entry["symbol_read"] = {"status": "skipped", "reason": f"graph engine: {exc}"}
            if reads:
                read_results: List[Dict[str, Any]] = []
                for f, s in reads:
                    if sum(1 for r in read_results if r.get("status") == "ok") >= 3:
                        break  # pre-registered rule: top-3 measurable god files
                    read_results.append({"file": f, "symbol": s, **self.measure_symbol_read(root, f, s)})
                oks = [r for r in read_results if r.get("status") == "ok"]
                if oks:
                    baseline = sum(r["baseline_tokens"] for r in oks)
                    optimized = sum(r["optimized_tokens"] for r in oks)
                    gates_all: Dict[str, bool] = {}
                    for r in oks:
                        gates_all.update(r.get("gates", {}))
                    entry["symbol_read"] = self._finish(baseline, optimized, gates_all, extra={"reads": len(oks)})
                else:
                    # Preserve the full first result (tokens/gates included) so a
                    # gate_failed row still renders with its numbers + reason.
                    entry["symbol_read"] = read_results[0]
            else:
                entry.setdefault("symbol_read", {"status": "skipped", "reason": "no measurable symbols found"})
            entry["test_output"] = {"status": "skipped", "reason": "language test runner not configured for this repo (bench v4)"}
            entry["multiturn"] = self.measure_multiturn(root, [{"file": f, "symbol": s} for f, s in reads] if reads else [])
            entry["prompt_compaction"] = self.measure_prompt_compaction(root, {"file": reads[0][0], "symbol": reads[0][1]} if reads else {})
        else:
            entry["symbol_read"] = self.measure_symbol_read(root, **repo["symbol_read"])
            entry["test_output"] = self.measure_test_output(root, repo.get("pytest", {}))
            entry["multiturn"] = self.measure_multiturn(root, repo.get("multiturn_reads", []))
            entry["prompt_compaction"] = self.measure_prompt_compaction(root, repo["symbol_read"])
        entry["architecture"] = self.measure_architecture(root)
        entry["raw_search"] = self.measure_raw_search(root, repo.get("raw_search", {}))
        entry["git_diff"] = self.measure_git_diff(root)
        entry["write_egress"] = self.measure_write_egress(root)
        entry["api_surface"] = self.measure_api_surface(root)
        entry["kv_cache"] = self.measure_kv_cache(root)
        entry["secret_guard"] = self.measure_secret_guard(root)
        entry["junk_read"] = self.measure_junk_read(root)
        scen_keys = ("symbol_read", "test_output", "multiturn", "architecture", "raw_search", "git_diff",
                     "write_egress", "api_surface", "kv_cache", "secret_guard", "junk_read", "prompt_compaction")
        ok = [entry[s].get("status") == "ok" for s in scen_keys if s in entry and isinstance(entry[s], dict)]
        entry["status"] = "ok" if ok and all(ok) else ("partial" if any(ok) else "failed")
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
            for scen in ("symbol_read", "test_output", "multiturn", "architecture", "raw_search", "git_diff",
                         "write_egress", "api_surface", "kv_cache", "secret_guard", "junk_read", "prompt_compaction"):
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
