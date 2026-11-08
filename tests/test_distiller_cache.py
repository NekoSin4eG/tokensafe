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

if __name__ == "__main__":
    unittest.main()
