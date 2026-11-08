"""
Unit tests for Managed Cloud Distillation (Server Endpoint & Client Auto-Delegation).
"""

import os
import sys
import unittest
import json
import hashlib
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.cloud_backend import (
    CloudBackendService,
    init_cloud_db,
    get_db_connection
)
from src.license_manager import LicenseManager
from src.distiller import UniversalDistiller

class TestManagedCloudDistillation(unittest.TestCase):

    def setUp(self):
        init_cloud_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM team_cache WHERE team_id LIKE '%@test.com'")
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@test.com'")
            conn.execute("DELETE FROM customers WHERE email LIKE '%@test.com'")
            conn.commit()

    def test_cloud_distill_rejects_free_license(self):
        free_key = "TS2.invalid.free"
        res = CloudBackendService.distill_prompt("Fix 401 JWT error in auth.ts", license_key=free_key)
        self.assertFalse(res["success"])
        self.assertIn("requires active", res["error"].lower())

    def test_cloud_distill_accepts_solo_license(self):
        solo_key = LicenseManager.generate_license("solo", "solo_dev@test.com", duration_days=30)
        res = CloudBackendService.distill_prompt(
            "Could you please rewrite and refactor the function get_users inside the file api.py so that it uses async and connects to database",
            license_key=solo_key
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["distilled"].startswith("("))
        self.assertTrue(res["distilled"].endswith(")"))

    def test_cloud_distill_hits_team_cache(self):
        team_key = LicenseManager.generate_license("team", "team_lead@test.com", duration_days=30)
        prompt = "Fix 401 JWT error in auth.ts when login via oauth"
        
        # Pre-seed team cache
        team_id = CloudBackendService.resolve_team_id(team_key)
        h = hashlib.sha256(" ".join(prompt.strip().split()).encode("utf-8")).hexdigest()
        CloudBackendService.team_cache_put(team_id, "PROMPT", h, '(DEBUG auth.ts [oauth jwt] "fix 401")', tokens_saved=50)

        # Distill should hit team cache
        res = CloudBackendService.distill_prompt(prompt, license_key=team_key)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("cached"))
        self.assertEqual(res["distilled"], '(DEBUG auth.ts [oauth jwt] "fix 401")')

    def test_client_is_self_hosted_toggle(self):
        with patch.dict(os.environ, {"TOKENSAFE_SELF_HOSTED": "1"}):
            self.assertTrue(UniversalDistiller.is_self_hosted())
        with patch.dict(os.environ, {"TOKENSAFE_SELF_HOSTED": "0"}):
            self.assertFalse(UniversalDistiller.is_self_hosted())

    @patch("urllib.request.urlopen")
    def test_client_cloud_distill_delegation(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "success": True,
            "distilled": '(OPTIMIZE cache.py get_data [redis] "add ttl")'
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        solo_key = LicenseManager.generate_license("solo", "dev@test.com", duration_days=30)
        with patch.dict(os.environ, {"TOKENSAFE_LICENSE_KEY": solo_key, "TOKENSAFE_SELF_HOSTED": ""}):
            res = UniversalDistiller.distill("Add 60s ttl to cache.py get_data using redis")
            self.assertEqual(res, '(OPTIMIZE cache.py get_data [redis] "add ttl")')

if __name__ == "__main__":
    unittest.main()
