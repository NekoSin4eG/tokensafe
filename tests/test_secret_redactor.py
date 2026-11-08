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

if __name__ == "__main__":
    unittest.main()
