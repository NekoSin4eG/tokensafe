import unittest
from unittest.mock import patch, MagicMock
import json
from src.distiller import UniversalDistiller

class TestUniversalDistiller(unittest.TestCase):

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("src.distiller.UniversalDistiller.get_configured_providers")
    @patch("urllib.request.urlopen")
    def test_openai_distillation(self, mock_urlopen, mock_get_providers, mock_license):
        mock_get_providers.return_value = {
            "openai": {
                "provider": "openai",
                "name": "OpenAI ChatGPT",
                "api_key": "sk-fake-openai",
                "model": "gpt-4o-mini",
                "timeout": 3.0,
                "endpoint": "https://api.openai.com/v1/chat/completions"
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '(REFACTOR db.py migrate [async] "use async engine")'}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = UniversalDistiller.distill("Please refactor db.py to migrate to async engine")
        self.assertEqual(res, '(REFACTOR db.py migrate [async] "use async engine")')

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("src.distiller.UniversalDistiller.get_configured_providers")
    @patch("urllib.request.urlopen")
    def test_gemini_distillation(self, mock_urlopen, mock_get_providers, mock_license):
        mock_get_providers.return_value = {
            "gemini": {
                "provider": "gemini",
                "name": "Google Gemini",
                "api_key": "AIzaFakeGemini",
                "model": "gemini-2.0-flash",
                "timeout": 3.0,
                "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": '(OPTIMIZE cache.py get_data [redis ttl] "add 60s ttl")'}]
                }
            }]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = UniversalDistiller.distill("Add 60 seconds TTL to cache.py get_data via redis")
        self.assertEqual(res, '(OPTIMIZE cache.py get_data [redis ttl] "add 60s ttl")')

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("src.distiller.UniversalDistiller.get_configured_providers")
    @patch("urllib.request.urlopen")
    def test_deepseek_distillation(self, mock_urlopen, mock_get_providers, mock_license):
        mock_get_providers.return_value = {
            "deepseek": {
                "provider": "deepseek",
                "name": "DeepSeek-V3",
                "api_key": "sk-fake-deepseek",
                "model": "deepseek-chat",
                "timeout": 3.0,
                "endpoint": "https://api.deepseek.com/chat/completions"
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '(DEBUG auth.ts [oauth] "fix timeout")'}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = UniversalDistiller.distill("Fix timeout in auth.ts when login via oauth")
        self.assertEqual(res, '(DEBUG auth.ts [oauth] "fix timeout")')

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"}))
    @patch("src.distiller.UniversalDistiller.get_configured_providers")
    @patch("urllib.request.urlopen")
    def test_fallback_between_providers(self, mock_urlopen, mock_get_providers, mock_license):
        # Gemini fails with timeout, OpenAI succeeds
        mock_get_providers.return_value = {
            "gemini": {
                "provider": "gemini",
                "name": "Google Gemini",
                "api_key": "AIzaFake",
                "model": "gemini-2.0-flash",
                "timeout": 3.0,
                "endpoint": "https://fake.gemini/{model}?key={key}"
            },
            "openai": {
                "provider": "openai",
                "name": "OpenAI ChatGPT",
                "api_key": "sk-fake-openai",
                "model": "gpt-4o-mini",
                "timeout": 3.0,
                "endpoint": "https://api.openai.com/v1/chat/completions"
            }
        }
        mock_resp_success = MagicMock()
        mock_resp_success.status = 200
        mock_resp_success.read.return_value = json.dumps({
            "choices": [{"message": {"content": '(TEST test_api.py [pytest async] "run async tests")'}}]
        }).encode("utf-8")
        mock_resp_success.__enter__.return_value = mock_resp_success

        mock_urlopen.side_effect = [
            TimeoutError("Gemini timed out"),
            mock_resp_success
        ]

        res = UniversalDistiller.distill("Run async tests in test_api.py with pytest")
        self.assertEqual(res, '(TEST test_api.py [pytest async] "run async tests")')

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("free", {"tier": "free"}))
    def test_free_tier_blocks_distillation(self, mock_license):
        res = UniversalDistiller.distill("Please refactor db.py to migrate to async engine")
        self.assertIsNone(res)

    @patch("src.license_manager.LicenseManager.get_active_license", return_value=("free", {"tier": "free"}))
    def test_free_tier_blocks_configured_providers(self, mock_license):
        configured = UniversalDistiller.get_configured_providers()
        self.assertEqual(configured, {})

if __name__ == "__main__":
    unittest.main()
