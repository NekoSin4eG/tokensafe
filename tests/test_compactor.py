import unittest
from src.compactor import PromptCompactor

class TestPromptCompactor(unittest.TestCase):
    def test_russian_compaction(self):
        prompt = "Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "REFACTOR")
        self.assertEqual(res["target_file"], "wallet.ts")
        self.assertEqual(res["symbol"], "get_balance")
        self.assertIn("async", res["traits"])
        self.assertIn("redis", res["traits"])
        self.assertGreater(res["saved_tokens"], 15)
        self.assertGreater(res["reduction_pct"], 35.0)

    def test_english_compaction(self):
        prompt = "Please refactor the function calculate_tax in src/billing.py to be async and cache in redis"
        res = PromptCompactor.compact(prompt, enable_distill=False)
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
        # Prompts below 100 tokens must use local zero-cost REGEX even if enable_distill=True
        prompt = "Пожалуйста, исправь функцию authenticate в auth.ts, чтобы она проверяла jwt токен"
        res = PromptCompactor.compact(prompt, enable_distill=True)
        self.assertEqual(res["tier"], "REGEX")
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.ts")

    def test_threshold_custom_min_distill_tokens(self):
        from unittest.mock import patch
        prompt = "Пожалуйста, поправь функцию authenticate в auth.ts, чтобы она проверяла jwt токен"
        with patch("src.license_manager.LicenseManager.get_active_license", return_value=("solo", {"tier": "solo"})), \
             patch("src.distiller.UniversalDistiller.distill") as mock_distill:
            mock_distill.return_value = '(DEBUG auth.ts [jwt] "verify token")'
            res = PromptCompactor.compact(prompt, enable_distill=True, min_distill_tokens=10)
            self.assertEqual(res["tier"], "DISTILLER")
            self.assertEqual(res["compact_text"], '(DEBUG auth.ts [jwt] "verify token")')
            mock_distill.assert_called_once()

    def test_free_tier_skips_neural_distillation(self):
        from unittest.mock import patch
        prompt = "Пожалуйста, поправь функцию authenticate в auth.ts, чтобы она проверяла jwt токен"
        with patch("src.license_manager.LicenseManager.get_active_license", return_value=("free", {"tier": "free"})), \
             patch("src.distiller.UniversalDistiller.distill") as mock_distill:
            res = PromptCompactor.compact(prompt, enable_distill=True, min_distill_tokens=10)
            self.assertEqual(res["tier"], "REGEX")
            mock_distill.assert_not_called()

    def test_special_files_detection(self):
        # Dotfiles and extensionless files must be recognized as target_file
        res_docker = PromptCompactor.compact("Dockerfile не собирается, исправь ошибку сборки", enable_distill=False)
        self.assertEqual(res_docker["target_file"], "Dockerfile")
        self.assertEqual(res_docker["action"], "DEBUG")

        res_env = PromptCompactor.compact("нужно настроить .env и docker для postgres базы данных", enable_distill=False)
        self.assertEqual(res_env["target_file"], ".env")
        self.assertEqual(res_env["action"], "CONFIG")
        self.assertIn("postgres", res_env["traits"])
        self.assertIn("docker", res_env["traits"])

        res_docs = PromptCompactor.compact("обнови README.md и добавь документацию gemini", enable_distill=False)
        self.assertEqual(res_docs["target_file"], "README.md")
        self.assertEqual(res_docs["action"], "DOCS")
        self.assertIn("gemini", res_docs["traits"])

    def test_action_priorities(self):
        # 'напиши юнит тесты' must prioritize TEST over CREATE
        res_test = PromptCompactor.compact("напиши юнит тесты на docker compose и redis в service.ts", enable_distill=False)
        self.assertEqual(res_test["action"], "TEST")
        self.assertEqual(res_test["target_file"], "service.ts")
        self.assertIn("unit_test", res_test["traits"])

        # 'задеплой' must map to DEPLOY
        res_deploy = PromptCompactor.compact("задеплой проект в bunny и проверь статус", enable_distill=False)
        self.assertEqual(res_deploy["action"], "DEPLOY")
        self.assertNotIn("bunny", res_deploy["traits"])

    def test_symbol_backticks_and_cases(self):
        # Russian case inflections ('в методе')
        res_case = PromptCompactor.compact("в методе validateUser в auth.py добавь jwt проверку", enable_distill=False)
        self.assertEqual(res_case["symbol"], "validateUser")
        self.assertEqual(res_case["target_file"], "auth.py")
        self.assertIn("jwt", res_case["traits"])

        # Backticked identifiers
        res_tick = PromptCompactor.compact("пожалуйста проверь безопасность jwt токена в `verifySession`", enable_distill=False)
        self.assertEqual(res_tick["symbol"], "verifySession")
        self.assertEqual(res_tick["action"], "AUDIT")
        self.assertIn("jwt", res_tick["traits"])

    def test_multilingual_spanish(self):
        prompt = "Por favor arregla la función validate_token en auth.py para que verifique jwt y devuelva boolean"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])
        self.assertGreater(res["saved_tokens"], 0)

    def test_multilingual_german(self):
        prompt = "Bitte repariere die Funktion validate_token in auth.py damit es jwt überprüft"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_french(self):
        prompt = "S'il vous plaît corriger la fonction validate_token dans auth.py pour vérifier le token jwt"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_chinese(self):
        prompt = "请修复 auth.py 中的 validate_token 函数并添加 jwt 验证"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

    def test_multilingual_japanese(self):
        prompt = "auth.py の validate_token 関数を修正して jwt トークンを検証してください"
        res = PromptCompactor.compact(prompt, enable_distill=False)
        self.assertEqual(res["action"], "DEBUG")
        self.assertEqual(res["target_file"], "auth.py")
        self.assertEqual(res["symbol"], "validate_token")
        self.assertIn("jwt", res["traits"])

if __name__ == "__main__":
    unittest.main()


class TestFileRegexExtensionIntegrity(unittest.TestCase):
    """QA sweep finding: the extension alternation listed short suffixes before
    long ones with no trailing \\b, so FILE_REGEX truncated extracted target
    files (App.tsx -> App.ts) — DISTILLER-tier target_file metadata was wrong
    for every .tsx/.jsx/.cpp/.hpp prompt. Ruby/PHP/Swift/Kotlin were missing
    entirely."""

    def test_no_extension_truncation(self):
        for path in ("packages/x/components/App.tsx", "src/a.jsx", "lib/b.cpp",
                     "inc/c.hpp", "d.rb", "e.php", "f.swift", "g.kt",
                     "h.mjs", "i.cjs", "j.mdx", "k.vue", "l.scala"):
            self.assertIn(path, PromptCompactor.FILE_REGEX.findall(path),
                          f"extension truncated or missing for {path}")

    def test_prefix_safe_ordering(self):
        exts = PromptCompactor._FILE_EXTS
        for short in ("ts", "js", "c", "h", "md"):
            for e in exts:
                if e != short and e.endswith(short):
                    self.assertLess(exts.index(e), exts.index(short),
                                    f"{e} must precede {short} (longest-first)")
