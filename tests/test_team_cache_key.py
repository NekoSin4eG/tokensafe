import os
import shutil
import tempfile
import unittest

from src import mcp_server


class TestTeamCacheKey(unittest.TestCase):
    def test_portable_key_matches_across_different_machine_roots(self):
        root_a = tempfile.mkdtemp(prefix="alice_repo_")
        root_b = tempfile.mkdtemp(prefix="bob_repo_")
        self.addCleanup(shutil.rmtree, root_a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root_b, ignore_errors=True)

        for r in (root_a, root_b):
            os.makedirs(os.path.join(r, ".git"))
            os.makedirs(os.path.join(r, "src"))
            with open(os.path.join(r, "src", "svc.py"), "w", encoding="utf-8") as f:
                f.write("def alpha(x):\n    return x + 1\n")

        key_a = mcp_server._team_cache_key(os.path.join(root_a, "src", "svc.py"), "alpha", dep_mode="signature")
        key_b = mcp_server._team_cache_key(os.path.join(root_b, "src", "svc.py"), "alpha", dep_mode="signature")
        self.assertEqual(key_a, key_b)
        self.assertTrue(key_a.startswith("src/svc.py::alpha::signature::"))

    def test_key_invalidates_on_file_edit_and_separates_dep_modes(self):
        root = tempfile.mkdtemp(prefix="repo_edit_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, ".git"))
        file_p = os.path.join(root, "mod.py")
        with open(file_p, "w", encoding="utf-8") as f:
            f.write("def beta():\n    return 1\n")

        k_sig = mcp_server._team_cache_key(file_p, "beta", dep_mode="signature")
        k_full = mcp_server._team_cache_key(file_p, "beta", dep_mode="full")
        self.assertNotEqual(k_sig, k_full)

        with open(file_p, "w", encoding="utf-8") as f:
            f.write("def beta():\n    return 2\n")
        k_sig_after = mcp_server._team_cache_key(file_p, "beta", dep_mode="signature")
        self.assertNotEqual(k_sig, k_sig_after)


if __name__ == "__main__":
    unittest.main()
