import unittest
import tempfile
import os
import shutil
from src.distiller_cache import DistillationCache

class TestDistillationCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = os.environ.get("TOKENSAFE_DATA_DIR")
        os.environ["TOKENSAFE_DATA_DIR"] = self.temp_dir
        DistillationCache._db_path = None
        DistillationCache.clear()

    def tearDown(self):
        if self.orig_data_dir:
            os.environ["TOKENSAFE_DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
        DistillationCache._db_path = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cache_miss_and_set_get(self):
        prompt = "Refactor auth.ts to support OAuth2 google login"
        self.assertIsNone(DistillationCache.get(prompt))

        s_expr = '(REFACTOR auth.ts [oauth google] "support oauth2 login")'
        DistillationCache.set(prompt, s_expr, provider="deepseek")

        cached = DistillationCache.get(prompt)
        self.assertEqual(cached, s_expr)

        stats = DistillationCache.get_stats()
        self.assertEqual(stats["memory_entries"], 1)
        self.assertEqual(stats["persistent_entries"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)

    def test_whitespace_normalization(self):
        p1 = "  Refactor   auth.ts  to  use   redis   "
        p2 = "Refactor auth.ts to use redis"
        s_expr = '(REFACTOR auth.ts [redis] "use redis")'

        DistillationCache.set(p1, s_expr)
        self.assertEqual(DistillationCache.get(p2), s_expr)

    def test_persistence_across_memory_clear(self):
        prompt = "Fix deadlock in order_service.py"
        s_expr = '(DEBUG order_service.py [mutex] "fix deadlock")'
        DistillationCache.set(prompt, s_expr)

        # Clear only in-memory cache
        with DistillationCache._lock:
            DistillationCache._memory_cache.clear()

        # Should still fetch from SQLite
        cached = DistillationCache.get(prompt)
        self.assertEqual(cached, s_expr)

    def test_parametric_template_slot_hit(self):
        p1 = "Refactor get_balance in wallet.ts to add redis caching"
        s1 = '(REFACTOR wallet.ts get_balance [redis] "add redis caching")'
        DistillationCache.set(p1, s1, provider="deepseek", sync_remote=False)

        # Structurally identical prompt with different symbol and file -> zero-LLM template hit
        p2 = "refactor validateToken in auth.ts to add redis caching"
        cached = DistillationCache.get(p2)
        self.assertEqual(cached, '(REFACTOR auth.ts validateToken [redis] "add redis caching")')

    def test_parametric_template_skips_unresolved_slots(self):
        p1 = "Refactor get_balance in wallet.ts to add redis"
        s1 = '(REFACTOR wallet.ts get_balance [redis] "add redis")'
        DistillationCache.set(p1, s1, provider="deepseek", sync_remote=False)

        # Missing <FILE_0> slot -> must not leak unresolved <FILE_0> placeholder
        p2 = "Refactor check_auth to add redis"
        self.assertIsNone(DistillationCache.get(p2))

    def test_parametric_template_survives_memory_clear_and_repeated_slots(self):
        p1 = "Sync handleWebhook in src/api/webhook.ts with handleWebhook tests"
        s1 = '(SYNC src/api/webhook.ts handleWebhook "with tests")'
        DistillationCache.set(p1, s1, provider="deepseek", sync_remote=False)

        with DistillationCache._lock:
            DistillationCache._memory_cache.clear()
            DistillationCache._template_memory_cache.clear()

        p2 = "sync verifySignature in src/auth/jwt.ts with verifySignature tests"
        self.assertEqual(
            DistillationCache.get(p2),
            '(SYNC src/auth/jwt.ts verifySignature "with tests")',
        )
        self.assertIsNone(DistillationCache.get(""))

if __name__ == "__main__":
    unittest.main()
