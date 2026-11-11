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

    def test_explicit_env_wins_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TOKENSAFE_DATA_DIR"] = tmp
            os.environ["PLUGIN_DATA"] = os.path.join(tmp, "pdata")
            self.assertEqual(paths.resolve_data_dir(), tmp)

    def test_canonical_default_is_user_level(self):
        os.environ.pop("TOKENSAFE_DATA_DIR", None)
        os.environ.pop("PLUGIN_DATA", None)
        self.assertEqual(paths.resolve_data_dir(), paths.user_data_dir())
        self.assertEqual(
            paths.user_data_dir(),
            os.path.join(os.path.expanduser("~"), ".tokensafe", "data"),
        )

    def test_plugin_data_never_wins_resolution(self):
        """PLUGIN_DATA is a migration source only — resolution must be
        identical for every agent on the machine."""
        os.environ.pop("TOKENSAFE_DATA_DIR", None)
        os.environ["PLUGIN_DATA"] = "/some/plugin/data"
        with patch.object(paths, "user_data_dir", return_value="/tmp/canonical"):
            self.assertEqual(paths.resolve_data_dir(), "/tmp/canonical")


class TestMigration(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("TOKENSAFE_DATA_DIR", "PLUGIN_DATA")}

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_migrates_from_legacy_repo_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("db-bytes")
            with open(os.path.join(legacy, "license.json"), "w", encoding="utf-8") as f:
                f.write('{"license_key": "k"}')

            canonical = os.path.join(tmp, "canonical")
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ.pop("PLUGIN_DATA", None)
            with patch.object(paths, "PROJECT_ROOT", repo), \
                 patch.object(paths, "user_data_dir", return_value=canonical):
                self.assertEqual(paths.resolve_data_dir(), canonical)

            self.assertTrue(os.path.isfile(os.path.join(canonical, "metrics.db")))
            with open(os.path.join(canonical, "metrics.db"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "db-bytes")
            self.assertTrue(os.path.isfile(os.path.join(canonical, "license.json")))

    def test_migrates_from_plugin_data_when_legacy_lacks_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            pdata = os.path.join(tmp, "pdata")
            os.makedirs(pdata)
            with open(os.path.join(pdata, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("plugin-db")

            canonical = os.path.join(tmp, "canonical")
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = pdata
            with patch.object(paths, "PROJECT_ROOT", repo), \
                 patch.object(paths, "user_data_dir", return_value=canonical):
                paths.resolve_data_dir()

            with open(os.path.join(canonical, "metrics.db"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "plugin-db")

    def test_legacy_source_is_authoritative_over_plugin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            pdata = os.path.join(tmp, "pdata")
            os.makedirs(pdata)
            with open(os.path.join(legacy, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("legacy-db")
            with open(os.path.join(pdata, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("plugin-db")

            canonical = os.path.join(tmp, "canonical")
            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ["PLUGIN_DATA"] = pdata
            with patch.object(paths, "PROJECT_ROOT", repo), \
                 patch.object(paths, "user_data_dir", return_value=canonical):
                paths.resolve_data_dir()

            with open(os.path.join(canonical, "metrics.db"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "legacy-db")

    def test_never_overwrites_existing_target_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "version_cache.json"), "w", encoding="utf-8") as f:
                f.write("new-stale")

            canonical = os.path.join(tmp, "canonical")
            os.makedirs(canonical)
            with open(os.path.join(canonical, "version_cache.json"), "w", encoding="utf-8") as f:
                f.write("old-kept")

            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ.pop("PLUGIN_DATA", None)
            with patch.object(paths, "PROJECT_ROOT", repo), \
                 patch.object(paths, "user_data_dir", return_value=canonical):
                paths.resolve_data_dir()

            with open(os.path.join(canonical, "version_cache.json"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "old-kept")
            leftovers = [f for f in os.listdir(canonical) if f.endswith(".migrating")]
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
            repo = os.path.join(tmp, "repo")
            legacy = os.path.join(repo, "data")
            os.makedirs(legacy)
            with open(os.path.join(legacy, "metrics.db"), "w", encoding="utf-8") as f:
                f.write("db")

            os.environ.pop("TOKENSAFE_DATA_DIR", None)
            os.environ.pop("PLUGIN_DATA", None)
            with patch.object(paths, "PROJECT_ROOT", repo), \
                 patch.object(paths, "user_data_dir", return_value=legacy):
                self.assertEqual(paths.resolve_data_dir(), legacy)
            # Source file untouched, no partial-copy artifacts
            with open(os.path.join(legacy, "metrics.db"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "db")
            leftovers = [f for f in os.listdir(legacy) if f.endswith(".migrating")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
