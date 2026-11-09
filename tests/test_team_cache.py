"""
Unit tests for TokenSafe Shared Team Cache (Client & Cloud Backend).
"""

import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["TOKENSAFE_PRIVATE_KEY"] = "65b68297d7a3901d8a4b3287b7757f11e7cfc34377a21b039d0f750d643ad9d5"

try:
    from src.cloud_backend import (
        CloudBackendService,
        init_cloud_db,
        get_db_connection
    )
except ImportError:
    CloudBackendService = None
    init_cloud_db = None
    get_db_connection = None

from src.license_manager import LicenseManager
from src.team_cache import TeamCache

@unittest.skipIf(CloudBackendService is None, "src.cloud_backend is a server-side component not distributed in client repo")
class TestSharedTeamCache(unittest.TestCase):

    def setUp(self):
        init_cloud_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM team_cache WHERE team_id LIKE '%@test.com'")
            conn.execute("DELETE FROM licenses WHERE customer_email LIKE '%@test.com'")
            conn.execute("DELETE FROM customers WHERE email LIKE '%@test.com'")
            conn.commit()

    def test_team_cache_backend_put_and_get(self):
        team_id = "org_lead@test.com"
        cache_type = "PROMPT"
        key_hash = TeamCache.hash_key("Fix 401 JWT error in auth.ts")
        value = '(DEBUG auth.ts [jwt] "fix 401 error")'

        # Put into cache
        ok = CloudBackendService.team_cache_put(team_id, cache_type, key_hash, value, tokens_saved=60)
        self.assertTrue(ok)

        # First Get
        res1 = CloudBackendService.team_cache_get(team_id, cache_type, key_hash)
        self.assertIsNotNone(res1)
        self.assertTrue(res1["hit"])
        self.assertEqual(res1["cached_value"], value)
        self.assertEqual(res1["tokens_saved"], 60)
        self.assertEqual(res1["hit_count"], 2)  # initial 1 + 1 hit

        # Second Get
        res2 = CloudBackendService.team_cache_get(team_id, cache_type, key_hash)
        self.assertEqual(res2["hit_count"], 3)

        # Non-existent key
        miss = CloudBackendService.team_cache_get(team_id, cache_type, "nonexistenthash123")
        self.assertIsNone(miss)

        # Team Stats
        stats = CloudBackendService.team_cache_stats(team_id)
        self.assertEqual(stats["entries_count"], 1)
        self.assertEqual(stats["total_hits"], 3)
        self.assertEqual(stats["total_tokens_saved"], 180)  # 60 * 3 hits

    def test_resolve_team_id_permissions(self):
        # 1. Solo license should NOT have access to team cache
        solo_key = LicenseManager.generate_license("solo", "solo@test.com", duration_days=30)
        team_id_solo = CloudBackendService.resolve_team_id(solo_key)
        self.assertIsNone(team_id_solo)

        # 2. Team master license should resolve to owner email
        team_owner_key = LicenseManager.generate_license("team", "cto@test.com", duration_days=30)
        team_id_owner = CloudBackendService.resolve_team_id(team_owner_key)
        self.assertEqual(team_id_owner, "cto@test.com")

        # 3. Team member seat license should resolve to parent_email
        member_key = LicenseManager.generate_license("team", "dev1@test.com", duration_days=30)
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO licenses (license_key, customer_email, tier, is_trial, is_seat, parent_email, status, created_at, expires_at)
                VALUES (?, 'dev1@test.com', 'team', 0, 1, 'cto@test.com', 'active', 1000, 2000)
            """, (member_key,))
            conn.commit()

        team_id_member = CloudBackendService.resolve_team_id(member_key)
        self.assertEqual(team_id_member, "cto@test.com")

        # 4. Revoked seat license should be rejected
        revoked_key = LicenseManager.generate_license("team", "dev2@test.com", duration_days=30)
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO licenses (license_key, customer_email, tier, is_trial, is_seat, parent_email, status, created_at, expires_at)
                VALUES (?, 'dev2@test.com', 'team', 0, 1, 'cto@test.com', 'revoked', 1000, 2000)
            """, (revoked_key,))
            conn.commit()

        team_id_revoked = CloudBackendService.resolve_team_id(revoked_key)
        self.assertIsNone(team_id_revoked)

    def test_client_skips_when_not_team(self):
        # If user is free or solo, TeamCache.get returns None immediately with 0 network calls
        with patch.object(TeamCache, "_is_team_tier", return_value=(False, "")):
            res = TeamCache.get("PROMPT", "Some test prompt")
            self.assertIsNone(res)

    @patch("urllib.request.urlopen")
    def test_client_hits_team_cache_when_team_tier(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "hit": True,
            "value": '(DEBUG auth.ts [jwt] "fix 401 error")',
            "hit_count": 2
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.object(TeamCache, "_is_team_tier", return_value=(True, "TS2.fake.key")):
            res = TeamCache.get("PROMPT", "Fix 401 JWT error in auth.ts")
            self.assertEqual(res, '(DEBUG auth.ts [jwt] "fix 401 error")')

    @patch("urllib.request.urlopen")
    def test_client_hits_team_cache_with_server_cached_value_field(self, mock_urlopen):
        # Verify compatibility when server responds with 'cached_value' field
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "hit": True,
            "cached_value": '(REFACTOR db.ts [conn] "pool connection")',
            "hit_count": 5
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.object(TeamCache, "_is_team_tier", return_value=(True, "TS2.fake.key")):
            res = TeamCache.get("PROMPT", "Refactor db connection to use pool")
            self.assertEqual(res, '(REFACTOR db.ts [conn] "pool connection")')

    @patch("urllib.request.urlopen")
    def test_client_graceful_timeout_fallback(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        with patch.object(TeamCache, "_is_team_tier", return_value=(True, "TS2.fake.key")):
            res = TeamCache.get("PROMPT", "Fix 401 JWT error in auth.ts")
            self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
