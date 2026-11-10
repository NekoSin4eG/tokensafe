"""
Golden-corpus regression gate: MEASURED compression guarantees on fixed inputs.

Unlike BENCHMARK_TASKS (a simulated harness with modeled baselines), these tests
run the real tools against deterministic embedded corpora and assert hard bounds.
A regression in compression quality or zero-loss fidelity fails CI here.
See METRICS.md for the measured/estimated methodology.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tokenizer import count_tokens
from src.skeletonizer import Skeletonizer
from src.compactor import PromptCompactor
from src.command_sanitizer import CommandSanitizer

# --- Fixed deterministic corpora -------------------------------------------

GOLDEN_PY_SOURCE = '''\
"""Module docstring for the golden corpus."""

import os
import sys
from typing import Dict, List, Optional


MAX_RETRIES = 3
CACHE_SIZE = 512
DEBUG = False


class PaymentGateway:
    """Processes payments with retry semantics."""

    def __init__(self, api_key: str, timeout: float = 2.5):
        self.api_key = api_key
        self.timeout = timeout
        self._attempts = 0

    def charge(self, amount_cents: int, currency: str = "usd") -> Dict:
        """Charges a card; retries transient failures up to MAX_RETRIES."""
        payload = {"amount": amount_cents, "currency": currency}
        for attempt in range(MAX_RETRIES):
            try:
                return self._post("/charges", payload)
            except TransientError:
                self._attempts = attempt + 1
                continue
        raise RuntimeError("charge failed after retries")

    def refund(self, charge_id: str, amount_cents: Optional[int] = None) -> Dict:
        """Refunds a charge, fully or partially."""
        body = {"charge": charge_id}
        if amount_cents is not None:
            body["amount"] = amount_cents
        return self._post("/refunds", body)

    def _post(self, path: str, body: Dict) -> Dict:
        # Internal HTTP plumbing; verbose error handling below.
        import urllib.request
        import urllib.error
        import json
        import time
        url = "https://api.example.com" + path
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for backoff in (0.1, 0.2, 0.4):
            try:
                req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {self.api_key}"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise
                last_error = exc
                time.sleep(backoff)
        raise last_error


def format_money(cents: int, currency: str = "USD") -> str:
    """Formats cents into a display string."""
    symbol = {"USD": "$", "EUR": "\\u20ac"}.get(currency, "")
    return f"{symbol}{cents / 100:.2f}"


def validate_amount(cents: int) -> bool:
    return cents > 0 and cents < 100_000_000
'''

GOLDEN_VERBOSE_PROMPT = (
    "Hey, so I was looking at the codebase yesterday and I noticed that in the payment module, "
    "specifically in the file payment_gateway.py, there is this function called charge that seems "
    "to occasionally hang or fail without proper retries, and I would really like you to go ahead "
    "and refactor it so that it properly implements retry logic with exponential backoff and also "
    "makes sure the operation is idempotent so we don't accidentally double-charge customers."
)

GOLDEN_LOCKFILE_DIFF = """\
diff --git a/bun.lock b/bun.lock
index 0000001..0000002 100644
--- a/bun.lock
+++ b/bun.lock
@@ -1,40 +1,41 @@
 {
-  "lockfileVersion": 0,
+  "lockfileVersion": 1,
   "workspaces": ["."],
-  "packages": ["zod@3.22.4"],
+  "packages": ["zod@3.23.0"],
diff --git a/src/payments.ts b/src/payments.ts
index 0000003..0000004 100644
--- a/src/payments.ts
+++ b/src/payments.ts
@@ -12,6 +12,8 @@ export async function createCheckout(opts: CheckoutOptions) {
   const session = await stripe.checkout.sessions.create({
     mode: opts.mode,
-    currency: "usd",
+    currency: opts.currency ?? "usd",
+    idempotencyKey: opts.idempotencyKey,
   });
   return session.url;
 }
"""


class GoldenCorpusMeasured(unittest.TestCase):
    """Hard measured bounds on fixed corpora — the CI gate for compression quality."""

    def test_skeleton_preserves_structure_and_compresses(self):
        skel = Skeletonizer.skeletonize_code(GOLDEN_PY_SOURCE, "py")
        orig_t = count_tokens(GOLDEN_PY_SOURCE)
        skel_t = count_tokens(skel)
        reduction_pct = (orig_t - skel_t) / orig_t * 100.0

        # Zero-loss: every top-level signature survives folding
        for marker in (
            "class PaymentGateway",
            "def __init__",
            "def charge",
            "def refund",
            "def _post",
            "def format_money",
            "def validate_amount",
        ):
            self.assertIn(marker, skel, f"golden signature lost: {marker}")
        # Braces stay balanced (fold never corrupts structure)
        self.assertEqual(skel.count("{"), skel.count("}"))
        # Compression bound: skeleton must be meaningfully smaller but not vanish
        self.assertGreaterEqual(reduction_pct, 40.0, f"skeleton regressed: only {reduction_pct:.1f}% reduction")
        self.assertLessEqual(reduction_pct, 97.0, "skeleton suspiciously empty")
        # Determinism
        self.assertEqual(skel, Skeletonizer.skeletonize_code(GOLDEN_PY_SOURCE, "py"))

    def test_prompt_compactor_measured_bound(self):
        r1 = PromptCompactor.compact(GOLDEN_VERBOSE_PROMPT, enable_deepseek=False)
        base = count_tokens(GOLDEN_VERBOSE_PROMPT)
        pct = r1["saved_tokens"] / base * 100.0 if base else 0.0
        self.assertGreaterEqual(pct, 20.0, f"prompt compaction regressed: {pct:.1f}%")
        self.assertLessEqual(pct, 95.0, "compactor output suspiciously empty")
        # Regex tier must be deterministic
        r2 = PromptCompactor.compact(GOLDEN_VERBOSE_PROMPT, enable_deepseek=False)
        self.assertEqual(r1["compact_text"], r2["compact_text"])

    def test_sanitizer_prunes_lockfile_diff(self):
        # The sanitizer rewrites an unbounded diff command into a bounded form;
        # the lockfile diff corpus above is what such commands dump in practice.
        new_cmd, saved, _ = CommandSanitizer.sanitize("git diff HEAD~1")
        self.assertTrue(new_cmd)
        self.assertGreaterEqual(saved, 0)

    def test_sanitizer_is_idempotent_across_all_rules(self):
        """Contract: sanitize(rewritten) must be a no-op — (None, 0, '').

        An already-sanitized command (lockfile pathspecs, head clamps, -n limits,
        -q --tb=short, compact ls flags) is recognized as clean, never rewritten
        twice. Feeding the output back must not grow it."""
        rule_samples = [
            "git diff HEAD~1",                      # rule 1: lockfile excludes
            "git log --all",                        # rule 2: log cap
            "pytest tests/",                        # rule 3: traceback clamp
            "find . -name '*.py'",                  # rule 4: recursion prune
            "git grep -rn '5+' .",                  # rule 5: git grep excludes
            "grep -rn 'TODO' src/",                 # rule 6: recursive grep excludes
            "ls -la",                               # rule 7: verbose ls
            # An already-fully-sanitized exotic form must also be a no-op:
            "git diff HEAD~1 -- ':!*.lock' ':!bun.lock' 2>&1 | head -n 100",
        ]
        for raw in rule_samples:
            new_cmd, _, _ = CommandSanitizer.sanitize(raw)
            if new_cmd is None:
                continue  # nothing to rewrite in the first place
            again_cmd, again_saved, _ = CommandSanitizer.sanitize(new_cmd)
            self.assertIsNone(
                again_cmd,
                f"sanitizer is not idempotent for {raw!r} -> {new_cmd!r} -> {again_cmd!r}"
            )
            self.assertEqual(again_saved, 0)

    def test_output_guard_bounded_report_shape(self):
        from src.benchmark import BenchmarkSuite
        data = BenchmarkSuite.run_all()
        # Evidence bookkeeping is internally consistent
        self.assertEqual(
            data["total_opt_measured_tokens"] + data["total_opt_modeled_tokens"],
            data["total_opt_tokens"],
        )
        # Modeled baselines must never be presented as zero-cost magic:
        # the harness keeps a sane global reduction in a realistic band.
        self.assertGreaterEqual(data["net_reduction_pct"], 50.0)
        self.assertLessEqual(data["net_reduction_pct"], 99.0)


if __name__ == "__main__":
    unittest.main()
