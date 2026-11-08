import unittest
from unittest.mock import patch, MagicMock
import io
import json
from src.deepseek_distiller import DeepSeekDistiller

class TestDeepSeekDistiller(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_distill_success(self, mock_urlopen):
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

    @patch("urllib.request.urlopen")
    def test_distill_timeout_fallback(self, mock_urlopen):
        # Simulate network timeout
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        res = DeepSeekDistiller.distill("Some long prompt that times out")
        self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
