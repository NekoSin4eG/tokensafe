import os
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import paths


class TestResolveDataDir(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("TOKENSAFE_DATA_DIR", "PLUGIN_DATA")}

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_explicit_env_wins_over_plugin_data_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TOKENSAFE_DATA_DIR"] = tmp
            os.environ["PLUGIN_DATA"] = os.path.join(tmp, "pdata")
            self.assertEqual(paths.resolve_data_dir(), tmp)

    def test_plugin_data_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = os.path.join(tmp, "pdata")
            self.assertEqual(paths.resolve_data_dir(), os.path.join(tmp, "pdata"))

    def test_legacy_fallback(self):
        os.environ.pop("TOKENSAFE_DATA_DIR", None)
        os.environ.pop("PLUGIN_DATA", None)
        self.assertEqual(paths.resolve_data_dir(), paths.legacy_data_dir())


class TestMigration(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("TOKENSAFE_DATA_DIR", "PLUGIN_DATA")}

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_plugin_data_migrates_legacy_files_copy_if_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("db-bytes")
            with open(os.path.join(legacy, "license.json"), "w", encoding="utf-8") as f:
                f.write('{"license_key": "k"}')

            pdata = os.path.join(tmp, "pdata")
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = pdata
            with patch.object(paths, "PROJECT_ROOT", repo):
                self.assertEqual(paths.resolve_data_dir(), pdata)

            self.assertTrue(os.path.isfile(os.path.join(pdata, "metrics.db")))
            with open(os.path.join(pdata, "metrics.db"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "db-bytes")
            self.assertTrue(os.path.isfile(os.path.join(pdata, "license.json")))

    def test_never_overwrites_existing_target_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "version_cache.json"), "w", encoding="utf-8") as f:
                f.write("new-stale")

            pdata = os.path.join(tmp, "pdata")
            os.makedirs(pdata)
            with open(os.path.join(pdata, "version_cache.json"), "w", encoding="utf-8") as f:
                f.write("old-kept")

            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = pdata
            with patch.object(paths, "PROJECT_ROOT", repo):
                paths.resolve_data_dir()

            with open(os.path.join(pdata, "version_cache.json"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "old-kept")
            leftovers = [f for f in os.listdir(pdata) if f.endswith(".migrating")]
            self.assertEqual(leftovers, [])

    def test_no_migration_on_explicit_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("db")

            explicit = os.path.join(tmp, "explicit")
            os.environ["TOKENSAFE_DATA_DIR"] = explicit
            os.environ.pop("PLUGIN_DATA", None)
            with patch.object(paths, "PROJECT_ROOT", repo):
                self.assertEqual(paths.resolve_data_dir(), explicit)
            self.assertFalse(os.path.exists(os.path.join(explicit, "metrics.db")))

    def test_no_self_migration_when_dirs_coincide(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = os.path.join(tmp, "repo", "data")
            with patch.object(paths, "PROJECT_ROOT", os.path.join(tmp, "repo")):
                # Must not raise even though source == target
                self.assertEqual(paths.resolve_data_dir(), os.path.join(tmp, "repo", "data"))


if __name__ == "__main__":
    unittest.main()
