import unittest
from src.compactor import PromptCompactor

class TestPromptCompactor(unittest.TestCase):
    def test_russian_compaction(self):
        prompt = "Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "REFACTOR")
        self.assertEqual(res["target_file"], "wallet.ts")
        self.assertEqual(res["symbol"], "get_balance")
        self.assertIn("async", res["traits"])
        self.assertIn("redis", res["traits"])
        self.assertGreater(res["saved_tokens"], 15)
        self.assertGreater(res["reduction_pct"], 35.0)

    def test_english_compaction(self):
        prompt = "Please refactor the function calculate_tax in src/billing.py to be async and cache in redis"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "REFACTOR")
        self.assertEqual(res["target_file"], "src/billing.py")
        self.assertEqual(res["symbol"], "calculate_tax")
        self.assertIn("async", res["traits"])
        self.assertGreaterEqual(res["saved_tokens"], 5)

    def test_short_command_passthrough(self):
        # Short CLI commands must pass through untouched
        prompt = "git status"
        res = PromptCompactor.compact(prompt)
        self.assertEqual(res["compact_text"], "git status")
        self.assertEqual(res["saved_tokens"], 0)

    def test_threshold_below_100_uses_regex(self):
        # Prompts below 100 tokens must use local zero-cost REGEX even if enable_deepseek=True
        prompt = "Пожалуйста, исправь функцию authenticate в auth.ts, чтобы она проверяла jwt токен"
        res = PromptCompactor.compact(prompt, enable_deepseek=True)
        self.assertEqual(res["tier"], "REGEX")
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.ts")

    def test_threshold_custom_min_distill_tokens(self):
        from unittest.mock import patch
        prompt = "Пожалуйста, поправь функцию authenticate в auth.ts, чтобы она проверяла jwt токен"
        with patch("src.distiller.UniversalDistiller.distill") as mock_distill:
            mock_distill.return_value = '(DEBUG auth.ts [jwt] "verify token")'
            res = PromptCompactor.compact(prompt, enable_deepseek=True, min_distill_tokens=10)
            self.assertEqual(res["tier"], "DEEPSEEK_V3")
            self.assertEqual(res["compact_text"], '(DEBUG auth.ts [jwt] "verify token")')
            mock_distill.assert_called_once()

    def test_special_files_detection(self):
        # Dotfiles and extensionless files must be recognized as target_file
        res_docker = PromptCompactor.compact("Dockerfile не собирается, исправь ошибку сборки", enable_deepseek=False)
        self.assertEqual(res_docker["target_file"], "Dockerfile")
        self.assertEqual(res_docker["action"], "DEBUG")

        res_env = PromptCompactor.compact("нужно настроить .env и docker для postgres базы данных", enable_deepseek=False)
        self.assertEqual(res_env["target_file"], ".env")
        self.assertEqual(res_env["action"], "CONFIG")
        self.assertIn("postgres", res_env["traits"])
        self.assertIn("docker", res_env["traits"])

        res_docs = PromptCompactor.compact("обнови README.md и добавь документацию gemini", enable_deepseek=False)
        self.assertEqual(res_docs["target_file"], "README.md")
        self.assertEqual(res_docs["action"], "DOCS")
        self.assertIn("gemini", res_docs["traits"])

    def test_action_priorities(self):
        # 'напиши юнит тесты' must prioritize TEST over CREATE
        res_test = PromptCompactor.compact("напиши юнит тесты на docker compose и redis в service.ts", enable_deepseek=False)
        self.assertEqual(res_test["action"], "TEST")
        self.assertEqual(res_test["target_file"], "service.ts")
        self.assertIn("unit_test", res_test["traits"])

        # 'задеплой' must map to DEPLOY
        res_deploy = PromptCompactor.compact("задеплой проект в bunny и проверь статус", enable_deepseek=False)
        self.assertEqual(res_deploy["action"], "DEPLOY")
        self.assertIn("bunny", res_deploy["traits"])

    def test_symbol_backticks_and_cases(self):
        # Russian case inflections ('в методе')
        res_case = PromptCompactor.compact("в методе validateUser в auth.py добавь jwt проверку", enable_deepseek=False)
        self.assertEqual(res_case["symbol"], "validateUser")
        self.assertEqual(res_case["target_file"], "auth.py")
        self.assertIn("jwt", res_case["traits"])

        # Backticked identifiers
        res_tick = PromptCompactor.compact("пожалуйста проверь безопасность jwt токена в `verifySession`", enable_deepseek=False)
        self.assertEqual(res_tick["symbol"], "verifySession")
        self.assertEqual(res_tick["action"], "AUDIT")
        self.assertIn("jwt", res_tick["traits"])

    def test_multilingual_spanish(self):
        prompt = "Por favor arregla la función validate_token en auth.py para que verifique jwt y devuelva boolean"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])
        self.assertGreater(res["saved_tokens"], 0)

    def test_multilingual_german(self):
        prompt = "Bitte repariere die Funktion validate_token in auth.py damit es jwt überprüft"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_french(self):
        prompt = "S'il vous plaît corriger la fonction validate_token dans auth.py pour vérifier le token jwt"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_chinese(self):
        prompt = "请修复 auth.py 中的 validate_token 函数并添加 jwt 验证"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_japanese(self):
        prompt = "auth.py の validate_token 関数を修正して jwt トークンを検証してください"
        res = PromptCompactor.compact(prompt, enable_deepseek=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

if __name__ == "__main__":
    unittest.main()
