import unittest
import os
from unittest.mock import patch, MagicMock
import io
import json
from src.deepseek_distiller import DeepSeekDistiller

class TestDeepSeekDistiller(unittest.TestCase):
    @patch.dict(os.environ, {"TOKENSAFE_SELF_HOSTED": "1", "DEEPSEEK_API_KEY": "test-key"})
    @patch("src.distiller_cache.DistillationCache.get", return_value=None)
    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("urllib.request.urlopen")
    def test_distill_success(self, mock_urlopen, mock_license, mock_cache):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_data = {
            "choices": [
                {
                    "message": {
                        "content": '(DEBUG auth.ts [jwt] "fix 401 error on login")'
                    }
                }
            ]
        }
        mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = DeepSeekDistiller.distill("У меня в auth.ts при логине падает с ошибкой 401 jwt expired")
        self.assertEqual(res, '(DEBUG auth.ts [jwt] "fix 401 error on login")')

    @patch.dict(os.environ, {"TOKENSAFE_SELF_HOSTED": "1", "DEEPSEEK_API_KEY": "test-key"})
    @patch("src.distiller_cache.DistillationCache.get", return_value=None)
    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("urllib.request.urlopen")
    def test_distill_timeout_fallback(self, mock_urlopen, mock_license, mock_cache):
        # Simulate network timeout
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        res = DeepSeekDistiller.distill("Some long prompt that times out")
        self.assertIsNone(res)

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("free", {"tier": "free"}))
    def test_distill_free_tier_blocked(self, mock_license):
        res = DeepSeekDistiller.distill("У меня в auth.ts при логине падает с ошибкой 401 jwt expired")
        self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
