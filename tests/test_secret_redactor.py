import unittest
from src.secret_redactor import SecretRedactor
from src.compactor import PromptCompactor

class TestSecretRedactor(unittest.TestCase):

    def test_stripe_keys(self):
        fake_stripe_live = "sk_live_" + "0" * 28
        fake_webhook = "whsec_" + "a" * 36
        text = f"Connect with key {fake_stripe_live} and webhook {fake_webhook}"
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 2)
        self.assertIn("stripe_secret", types)
        self.assertIn("stripe_webhook", types)
        self.assertNotIn(fake_stripe_live, redacted)
        self.assertNotIn(fake_webhook, redacted)
        self.assertIn("sk_live_mock_tokensafe_anchor", redacted)
        self.assertIn("whsec_mock_tokensafe_anchor", redacted)

    def test_aws_credentials(self):
        fake_aws_key = "AKIA" + "0" * 16
        fake_aws_secret = "wJalrXUtn" + "0" * 31
        text = f'AWS credentials: {fake_aws_key} and aws_secret_access_key="{fake_aws_secret}"'
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 2)
        self.assertIn("aws_access_key", types)
        self.assertIn("aws_secret_key", types)
        self.assertNotIn(fake_aws_key, redacted)
        self.assertNotIn(fake_aws_secret, redacted)
        self.assertIn("AKIA_MOCK_AWS_KEY_ID", redacted)
        self.assertIn("[REDACTED_AWS_SECRET_KEY]", redacted)

    def test_jwt_token(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Bearer {jwt}"
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 1)
        self.assertIn("jwt_token", types)
        self.assertNotIn(jwt, redacted)
        self.assertIn("eyJ_MOCK_JWT_PAYLOAD_ANCHOR", redacted)

    def test_database_connection_urls(self):
        pg_url = "postgres://" + "postgres:test_pass@internal-db:5432/main_db"
        mongo_url = "mongodb" + "+srv://test_user:test_secret@cluster0.test.net/app"
        text = f"Connect to {pg_url} or {mongo_url}"
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 2)
        self.assertIn("db_connection_url", types)
        self.assertNotIn("test_pass", redacted)
        self.assertNotIn("test_secret", redacted)
        self.assertIn("postgres://postgres:[REDACTED_DB_PWD]@internal-db:5432/main_db", redacted)
        self.assertIn("mongodb+srv://test_user:[REDACTED_DB_PWD]@cluster0.test.net/app", redacted)

    def test_ai_provider_keys(self):
        openai_key = "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
        anthropic_key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789-_ABCDEFGHIJKLMNOP"
        text = f"Keys: {openai_key} and {anthropic_key}"
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 2)
        self.assertNotIn(openai_key, redacted)
        self.assertNotIn(anthropic_key, redacted)
        self.assertIn("sk-proj-MOCK_OPENAI_KEY_ANCHOR", redacted)
        self.assertIn("sk-ant-MOCK_ANTHROPIC_KEY_ANCHOR", redacted)

    def test_private_key(self):
        rsa_key = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Y2X9w8s7d6f5g4h3j2k1l0m9n8b7v6c5x4z3a2s1d0f
-----END RSA PRIVATE KEY-----"""
        text = f"Private key config:\n{rsa_key}"
        redacted, count, types = SecretRedactor.redact_text(text)
        self.assertEqual(count, 1)
        self.assertIn("private_key", types)
        self.assertNotIn("MIIEowIBAAKCAQEA0Y2X9w8s7d6f5g4h3j2k1l0m9n8b7v6c5x4z3a2s1d0f", redacted)
        self.assertIn("[REDACTED_PRIVATE_KEY_MOCK_ANCHOR]", redacted)

    def test_redact_messages(self):
        fake_key = "sk_live_" + "9" * 28
        messages = [
            {"role": "system", "content": "You are a backend assistant with db: postgres://u:pwd123@localhost:5432/db"},
            {"role": "user", "content": f"Debug this Stripe key: {fake_key}"}
        ]
        redacted_msgs, total = SecretRedactor.redact_messages(messages)
        self.assertEqual(total, 2)
        self.assertNotIn("pwd123", redacted_msgs[0]["content"])
        self.assertNotIn(fake_key, redacted_msgs[1]["content"])
        self.assertIn("[REDACTED_DB_PWD]", redacted_msgs[0]["content"])
        self.assertIn("sk_live_mock_tokensafe_anchor", redacted_msgs[1]["content"])

    def test_compactor_secret_scrubbing(self):
        raw_prompt = "Пожалуйста, проверь подключение к базе данных postgres://admin:VerySecretPass99@db:5432/prod в файле server.ts и почини ошибку"
        res = PromptCompactor.compact(raw_prompt, enable_deepseek=False)
        self.assertNotIn("VerySecretPass99", res["compact_text"])
        self.assertIn("REDACTED_DB_PWD", res["compact_text"])

    def test_check_secret_fallback_python(self):
        # Dangerous fallback with Ed25519 hex private key
        code_bad = 'DEFAULT_ED25519_PRIVATE_KEY = os.environ.get("TOKENSAFE_PRIVATE_KEY", "65b68297d7a3901d8a4b3287b7757f11e7cfc34377a21b039d0f750d643ad9d5")'
        is_bad, reason = SecretRedactor.check_secret_fallback(code_bad, file_path="src/license_manager.py")
        self.assertTrue(is_bad)
        self.assertIn("TOKENSAFE_PRIVATE_KEY", reason)

        # Safe empty fallback
        code_good = 'DEFAULT_ED25519_PRIVATE_KEY = os.environ.get("TOKENSAFE_PRIVATE_KEY", "")'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_good, file_path="src/license_manager.py")
        self.assertFalse(is_bad)

        # Safe placeholder fallback
        code_mock = 'API_SECRET = os.environ.get("API_SECRET", "mock_secret_key")'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_mock, file_path="src/config.py")
        self.assertFalse(is_bad)

    def test_check_secret_fallback_javascript(self):
        # Dangerous fallback in TypeScript/JS
        _prefix = "sk" + "_live" + "_"
        code_bad = 'const key = process.env.STRIPE_SECRET_KEY || "' + _prefix + ('8' * 28) + '"'
        is_bad, reason = SecretRedactor.check_secret_fallback(code_bad, file_path="server/billing.ts")
        self.assertTrue(is_bad)

        # Safe placeholder
        code_good = 'const key = process.env.STRIPE_SECRET_KEY || "mock_key"'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_good, file_path="server/billing.ts")
        self.assertFalse(is_bad)

    def test_check_secret_fallback_public_key_allowed(self):
        # Public key must NOT be blocked (designed to be in client code)
        code_pub = 'DEFAULT_ED25519_PUBLIC_KEY = os.environ.get("TOKENSAFE_PUBLIC_KEY", "3911e2ffe5ad07ee23be392b88f238af302f9d3a0bf51881de8b5e0e6af71430")'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_pub, file_path="src/license_manager.py")
        self.assertFalse(is_bad)

    def test_check_secret_fallback_in_test_files_allowed(self):
        # Test fixture assignment in test files is permitted
        code_test = 'TEST_PRIV_HEX = "db13e3dd82937482937482937482937482937482937482937482937482937482"'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_test, file_path="tests/test_crypto.py")
        self.assertFalse(is_bad)

        # But live provider keys are blocked even in tests!
        _prefix = "sk" + "_live" + "_"
        code_live_stripe = 'STRIPE_KEY = "' + _prefix + ('7' * 28) + '"'
        is_bad, _ = SecretRedactor.check_secret_fallback(code_live_stripe, file_path="tests/test_crypto.py")
        self.assertTrue(is_bad)

if __name__ == "__main__":
    unittest.main()
