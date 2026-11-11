import unittest
from unittest.mock import patch, MagicMock
import os
import json
import io

try:
    from src.mailer import TokenSafeMailer
except ImportError:
    TokenSafeMailer = None

@unittest.skipIf(TokenSafeMailer is None, "src.mailer is a server-side component not distributed in client repo")
class TestTokenSafeMailer(unittest.TestCase):
    def test_parse_sender(self):
        name, alias, domain = TokenSafeMailer._parse_sender("TokenSafe <support@tokensafe.dev>")
        self.assertEqual(name, "TokenSafe")
        self.assertEqual(alias, "support")
        self.assertEqual(domain, "tokensafe.dev")

        name, alias, domain = TokenSafeMailer._parse_sender("support@tokensafe.dev")
        self.assertEqual(name, "TokenSafe")
        self.assertEqual(alias, "support")
        self.assertEqual(domain, "tokensafe.dev")

        name, alias, domain = TokenSafeMailer._parse_sender("billing", "tokensafe.dev")
        self.assertEqual(name, "TokenSafe")
        self.assertEqual(alias, "billing")
        self.assertEqual(domain, "tokensafe.dev")

    def test_send_raw_test_domain_short_circuit(self):
        # Any @test.com recipient returns True immediately without network call
        result = TokenSafeMailer._send_raw("dev@test.com", "Test Subject", "Body text", "<p>Body</p>")
        self.assertTrue(result)

    @patch("urllib.request.urlopen")
    def test_send_via_api_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"id": "msg_123", "message_id": "<abc@tokensafe.dev>"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = TokenSafeMailer._send_via_api(
            api_key="sk_test_key_12345",
            domain="tokensafe.dev",
            from_alias="support",
            display_name="TokenSafe",
            recipient="alice@company.com",
            subject="Hello",
            text_content="Hello world",
            html_content="<p>Hello world</p>"
        )

        self.assertTrue(result)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.improvmx.com/v4/domains/tokensafe.dev/emails/outbound")
        self.assertEqual(req.headers["Content-type"], "application/json")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["from"], "support")
        self.assertEqual(payload["name"], "TokenSafe")
        self.assertEqual(payload["to"], "alice@company.com")

    @patch.dict(os.environ, {"IMPROVMX_API_KEY": "sk_mock_api_key_123"})
    @patch.object(TokenSafeMailer, "_send_via_api", return_value=True)
    def test_send_raw_uses_api_key(self, mock_send_api):
        result = TokenSafeMailer._send_raw("bob@customer.com", "Subject", "Text", "<p>HTML</p>")
        self.assertTrue(result)
        mock_send_api.assert_called_once()
        self.assertEqual(mock_send_api.call_args[1]["api_key"], "sk_mock_api_key_123")

    @patch.dict(os.environ, {"SMTP_PASS": "sk_detected_from_smtp_pass"}, clear=False)
    @patch.object(TokenSafeMailer, "_send_via_api", return_value=True)
    def test_send_raw_detects_sk_in_smtp_pass(self, mock_send_api):
        with patch.dict(os.environ, {"IMPROVMX_API_KEY": ""}):
            result = TokenSafeMailer._send_raw("bob@customer.com", "Subject", "Text", "<p>HTML</p>")
            self.assertTrue(result)
            mock_send_api.assert_called_once()
            self.assertEqual(mock_send_api.call_args[1]["api_key"], "sk_detected_from_smtp_pass")

    @patch.dict(os.environ, {"IMPROVMX_API_KEY": ""}, clear=False)
    @patch.object(TokenSafeMailer, "_send_via_smtp", return_value=True)
    def test_send_raw_falls_back_to_smtp_when_no_api_key(self, mock_send_smtp):
        with patch.dict(os.environ, {"SMTP_PASS": "legacy_password_123"}):
            result = TokenSafeMailer._send_raw("bob@customer.com", "Subject", "Text", "<p>HTML</p>")
            self.assertTrue(result)
            mock_send_smtp.assert_called_once()

    def test_send_raw_records_last_error_when_unconfigured(self):
        with patch.dict(os.environ, {"IMPROVMX_API_KEY": "", "SMTP_PASS": "", "IMPROVMX_SMTP_PASS": ""}, clear=False), \
             patch.object(TokenSafeMailer, "IMPROVMX_API_KEY", ""), \
             patch.object(TokenSafeMailer, "SMTP_PASS", ""):
            result = TokenSafeMailer._send_raw("bob@customer.com", "Subject", "Text", "<p>HTML</p>")
            self.assertFalse(result)
            self.assertIn("not configured", TokenSafeMailer.get_last_error().lower())

if __name__ == "__main__":
    unittest.main()
