import unittest
from src.tokenizer import TokenCounter, count_tokens

class TestTokenizer(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(count_tokens(""), 0)
        self.assertEqual(count_tokens(None), 0)

    def test_english_words(self):
        tokens = count_tokens("Hello world this is a test")
        self.assertGreaterEqual(tokens, 6)
        self.assertLessEqual(tokens, 10)

    def test_cyrillic_density(self):
        # Russian words are typically 1.5-2x BPE tokens due to multibyte encoding
        tokens = count_tokens("Привет мир это тестовый запрос")
        self.assertGreaterEqual(tokens, 8)

    def test_code_identifiers(self):
        tokens = count_tokens("getUserAccountBalance(userId, apiKey)")
        self.assertGreaterEqual(tokens, 8)

if __name__ == "__main__":
    unittest.main()
