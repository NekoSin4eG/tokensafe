"""
Semantic Compactor: Converts verbose natural language prompts (Russian/English)
into ultra-dense Token-Optimized Communication Protocol (TOCP) S-Expressions.
Supports 3-Tier Routing: Pass-through -> Regex Compactor -> DeepSeek-V3 Distiller.
"""

import os
import json
import re
from typing import Dict, List, Optional, Any
from src.tokenizer import count_tokens
from src.distiller import UniversalDistiller
from src.secret_redactor import SecretRedactor

class PromptCompactor:
    """Transforms verbose natural language requests into compact Intent IR."""

    STOP_PHRASES = [
        # Russian courtesy, prepositions & fillers
        r"\b(пожалуйста|будь добр|будь любезен|если не трудно|заранее спасибо|спасибо|благодарю)\b",
        r"\b(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|хай|салют)\b",
        r"\b(слушай|смотри|короче|кстати|в общем|в общем-то|дело в том что)\b",
        r"\b(можешь ли ты|можешь|не мог бы ты|помоги мне|помоги пожалуйста|мне нужно|я хочу чтобы ты|я хочу|нужно чтобы|надо чтобы|надо|нужно|давай)\b",
        r"\b(подскажи как|подскажи пожалуйста|подскажи|расскажи как|сделай так чтобы|сделай пожалуйста|попробуй|попробуем)\b",
        r"\b(как думаешь|как считаешь|по твоему мнению|в чем проблема|почему|почему падает|не работает)\b",
        r"\b(в файле|из файла|внутри файла|файле|файл|функцию|функция|функции|метод|методе|метода|класс|классе|класса|код|кода|строку|строка|проект|проекта|проекте|описани\w*|пояснени\w*|информаци\w*|баз\w*\s+данных|баз[аеуоы]|бд|database)\b",
        r"\b(добавь туда|сделай её|сделай его|сделай их|чтобы было|чтобы он|чтобы она|чтобы оно|для этого|для того чтобы)\b",
        r"\b(и|в|на|с|при|по|к|из|у|о|об|от|под|над|для|не|бы|ли|же|то|это|этот|эта|эти|он|она|оно|они|ему|ей|им|их|его|ее)\b",
        
        # English courtesy, prepositions & fillers
        r"\b(please|kindly|could you please|could you|would you mind|would you|thank you|thanks in advance)\b",
        r"\b(hello|hi there|hi|hey|good morning|good evening|good afternoon)\b",
        r"\b(listen|look|basically|anyway|actually|to be honest)\b",
        r"\b(can you help me with|can you help me|can you|help me to|help me|i want you to|i would like you to|i need to|i want to|we need to|lets|let's)\b",
        r"\b(make sure to|make sure that|tell me how to|show me how to|what is the reason|how can i)\b",
        r"\b(in file|inside file|the function|the method|the class|file|function|method|class|project)\b",
        r"\b(add to it|make it|so that it)\b",
        r"\b(and|with|on|at|for|from|to|the|a|an|in|of|is|are|it|its|this|that|these|those)\b",
    ]

    ACTION_PATTERNS = [
        ("TEST", re.compile(r"\b(test|spec|assert|coverage|unit[\s_-]?test|e2e|тест\w*|протестируй\w*|покрыт\w*\s+тест\w*)\b", re.I)),
        ("DEPLOY", re.compile(r"\b(deploy|release|publish|ship|выкат\w*|задепло\w*|опублику\w*|релиз\w*)\b", re.I)),
        ("BENCHMARK", re.compile(r"\b(benchmark|bench|profil\w*|замер\w*|бенчмарк\w*|профайлинг\w*)\b", re.I)),
        ("DOCS", re.compile(r"\b(document|readme|docs?|docstring|swagger|документ\w*|доку\b|дока\b|документаци\w*|комментар\w*)\b", re.I)),
        ("CLEAN", re.compile(r"\b(clean(?:up)?|remove|delete|prune|vacuum|удал\w*|почист\w*|очист\w*|сотри)\b", re.I)),
        ("CONFIG", re.compile(r"\b(config(?:ure)?|setup|env|настро\w*|конфигур\w*|сконфигурируй)\b", re.I)),
        ("UPDATE", re.compile(r"\b(update|upgrade|bump|обнов\w*|актуализируй\w*|подними\s+верси\w*)\b", re.I)),
        ("OPTIMIZE", re.compile(r"\b(optimize|speed\s*up|perf|memory\s*leak|latency|ускор\w*|оптимизируй\w*|быстрее)\b", re.I)),
        ("REFACTOR", re.compile(r"\b(refactor|rewrite|restructure|перепиш\w*|отрефактор\w*|передела\w*|модернизируй\w*)\b", re.I)),
        ("DEBUG", re.compile(r"\b(fix|debug|resolve|error|bug|issue|broken|failing|crash|build\s*fail\w*|почин\w*|исправ\w*|баг\w*|ошибк\w*|не\s*работает|пада\w*|падени\w*|не\s*собирается|собирается|не\s*компилируется|сборк\w*|билд\w*)\b", re.I)),
        ("CREATE", re.compile(r"\b(create|add|implement|build|write|new|generate|созда\w*|добав\w*|напиш\w*|реализу\w*|сдела\w*)\b", re.I)),
        ("AUDIT", re.compile(r"\b(audit|review|check|security|analyze|провер\w*|проанализируй\w*|аудит\w*|ревью)\b", re.I)),
        ("RUN", re.compile(r"\b(run|execute|start|launch|запуст\w*|выполн\w*|старту\w*)\b", re.I)),
        ("EXPLAIN", re.compile(r"\b(explain|why|how\s+does|what\s+is|clarify|объясн\w*|почему|как\s+работает|что\s+делает|поясн\w*)\b", re.I)),
    ]

    FILE_REGEX = re.compile(
        r'(?:'
        r'(?:[\w\-\./\\]+[\/\\])?[\w\-\.]+\.(?:ts|js|tsx|jsx|py|go|rs|java|c|cpp|h|hpp|json|yaml|yml|md|proto|toml|sql|html|css|scss|sh|bash|zsh|lock|vue|svelte|graphql|prisma)'
        r'|(?:\.env(?:\.[\w\-]+)?|\.gitignore|\.dockerignore|\.prettierrc[\w\.\-]*|\.eslintrc[\w\.\-]*|\.npmignore|\.npmrc|\.yarnrc)'
        r'|\b(?:Dockerfile(?:\.[\w\-]+)?|Makefile|Procfile|Gemfile|Vagrantfile|Containerfile)\b'
        r'|\b(?:README(?:\.md)?|LICENSE|CHANGELOG(?:\.md)?)\b'
        r')',
        re.I
    )

    TRAIT_MAPPINGS = [
        (re.compile(r"\b(async|асинхронн\w*)\b", re.I), "async"),
        (re.compile(r"\b(sync|синхронн\w*)\b", re.I), "sync"),
        (re.compile(r"\b(redis|редис)\b", re.I), "redis"),
        (re.compile(r"\b(cache|кэш\w*|кеш\w*)\b", re.I), "cache"),
        (re.compile(r"\b(lru)\b", re.I), "lru"),
        (re.compile(r"\b(postgres|postgresql|постгрес)\b", re.I), "postgres"),
        (re.compile(r"\b(sqlite)\b", re.I), "sqlite"),
        (re.compile(r"\b(mysql)\b", re.I), "mysql"),
        (re.compile(r"\b(prisma|призма)\b", re.I), "prisma"),
        (re.compile(r"\b(docker|докер)\b", re.I), "docker"),
        (re.compile(r"\b(bunny|банни)\b", re.I), "bunny"),
        (re.compile(r"\b(jwt|токен\w*|auth|авторизац\w*)\b", re.I), "jwt"),
        (re.compile(r"\b(oauth)\b", re.I), "oauth"),
        (re.compile(r"\b(zero_alloc|без\s+аллокаций)\b", re.I), "zero_alloc"),
        (re.compile(r"\b(typesafe|типизац\w*|типобезопасн\w*|типами|типы)\b", re.I), "typesafe"),
        (re.compile(r"\b(tailwind)\b", re.I), "tailwind"),
        (re.compile(r"\b(unit[\s_-]?test|юнит\w*)\b", re.I), "unit_test"),
        (re.compile(r"\b(e2e)\b", re.I), "e2e"),
        (re.compile(r"\b(mock|мок\w*)\b", re.I), "mock"),
        (re.compile(r"\b(npm)\b", re.I), "npm"),
        (re.compile(r"\b(pypi)\b", re.I), "pypi"),
        (re.compile(r"\b(gemini|гемини)\b", re.I), "gemini"),
        (re.compile(r"\b(deepseek|дипсик)\b", re.I), "deepseek"),
        (re.compile(r"\b(swagger|openapi)\b", re.I), "swagger"),
        (re.compile(r"\b(rest|api)\b", re.I), "rest"),
        (re.compile(r"\b(graphql)\b", re.I), "graphql"),
        (re.compile(r"\b(grpc)\b", re.I), "grpc"),
        (re.compile(r"\b(websocket|ws)\b", re.I), "websocket"),
        (re.compile(r"\b(sse)\b", re.I), "sse"),
        (re.compile(r"\b(react)\b", re.I), "react"),
        (re.compile(r"\b(vite)\b", re.I), "vite"),
        (re.compile(r"\b(nextjs|next\.js)\b", re.I), "nextjs"),
        (re.compile(r"\b(git|github)\b", re.I), "git"),
        (re.compile(r"\b(ci|github_actions|action)\b", re.I), "ci"),
        (re.compile(r"\b(mcp)\b", re.I), "mcp"),
        (re.compile(r"\b(crypto|ed25519|aes)\b", re.I), "crypto"),
    ]

    SYMBOL_KEYWORDS = (
        r"(?:функци[яиейюь]*|function|"
        r"метод[аеуомы]*|method|"
        r"класс[аеуомы]*|class|"
        r"хэндлер\w*|handler|"
        r"эндпоинт\w*|endpoint|"
        r"компонент\w*|component|"
        r"роут\w*|route|"
        r"хук\w*|hook|"
        r"сервис\w*|service|"
        r"контроллер\w*|controller|"
        r"переменн[уюаяые]+|variable|"
        r"интерфейс\w*|interface|"
        r"тип\w*|type|"
        r"структур\w*|struct|"
        r"символ\w*|symbol)"
    )
    SYMBOL_REGEX_KEYWORD = re.compile(SYMBOL_KEYWORDS + r"\s+[`'\"\(]?([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
    SYMBOL_REGEX_BACKTICK = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")
    SYMBOL_REGEX_CALL = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\(\)")

    DEFAULT_MIN_DISTILL_TOKENS = 100

    @classmethod
    def _get_min_distill_tokens(cls) -> int:
        """Loads minimum token threshold for LLM distillation from config or returns 100."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "data", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    conf = json.load(f)
                    if "llm_distill_min_tokens" in conf:
                        return int(conf["llm_distill_min_tokens"])
            except Exception:
                pass
        return cls.DEFAULT_MIN_DISTILL_TOKENS

    @classmethod
    def compact(
        cls,
        raw_text: str,
        enable_deepseek: bool = True,
        min_distill_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Compresses input prompt via 3-Tier Architecture:
        - Tier 0: Pass-through for very short inputs (< 6 tokens)
        - Tier 1: Local Regex Compactor (0ms, 0 external tokens) for inputs < 100 tokens
        - Tier 2: LLM Distiller (DeepSeek / Gemini / OpenAI) for long/complex prompts (>= 100 tokens)
        """
        if not raw_text or not raw_text.strip():
            return {
                "original_text": raw_text or "",
                "compact_text": raw_text or "",
                "original_tokens": 0,
                "compact_tokens": 0,
                "saved_tokens": 0,
                "reduction_pct": 0.0,
                "action": "UNKNOWN",
                "target_file": "",
                "symbol": "",
                "traits": [],
                "tier": "PASS_THROUGH"
            }

        # Step 0: Local Secret Redaction (Stripe, AWS, JWT, DB URLs, Private Keys)
        scrubbed_text, secrets_scrubbed, _ = SecretRedactor.redact_text(raw_text)
        if secrets_scrubbed > 0:
            raw_text = scrubbed_text

        original_tokens = count_tokens(raw_text)

        # Tier 0: Short commands pass through
        if original_tokens < 6:
            return {
                "original_text": raw_text,
                "compact_text": raw_text.strip(),
                "original_tokens": original_tokens,
                "compact_tokens": original_tokens,
                "saved_tokens": 0,
                "reduction_pct": 0.0,
                "action": "RAW",
                "target_file": "",
                "symbol": "",
                "traits": [],
                "tier": "PASS_THROUGH"
            }

        # Tier 2: LLM Intent Distiller (Gemini / OpenAI / DeepSeek)
        min_distill = min_distill_tokens if min_distill_tokens is not None else cls._get_min_distill_tokens()
        if enable_deepseek and original_tokens >= min_distill:
            distilled = UniversalDistiller.distill(raw_text)
            if distilled:
                dist_tokens = count_tokens(distilled)
                if dist_tokens < original_tokens:
                    saved = original_tokens - dist_tokens
                    pct = (saved / original_tokens * 100.0)

                    # Extract action from S-Expression: e.g. "(DEBUG ..."
                    act_match = re.match(r"^\(([A-Z]+)", distilled)
                    action = act_match.group(1) if act_match else "TASK"

                    # Extract file if present
                    files = cls.FILE_REGEX.findall(distilled)
                    target_file = files[0] if files else ""

                    # Extract symbol if present in raw_text
                    sym_match = re.search(
                        r"(?:функци[юяие]|function|method|метод|class|класс|handler|хэндлер|symbol|переменн[уюая]+)\s+[`'\"]?([a-zA-Z0-9_]+)[`'\"]?",
                        raw_text,
                        re.I
                    )
                    symbol = sym_match.group(1) if sym_match else ""

                    # Extract traits if present in distilled S-Expression: e.g. [async redis ...]
                    trait_match = re.search(r"\[(.*?)\]", distilled)
                    traits = trait_match.group(1).split() if trait_match else []

                    return {
                        "original_text": raw_text,
                        "compact_text": distilled,
                        "original_tokens": original_tokens,
                        "compact_tokens": dist_tokens,
                        "saved_tokens": saved,
                        "reduction_pct": round(pct, 1),
                        "action": action,
                        "target_file": target_file,
                        "symbol": symbol,
                        "traits": traits,
                        "tier": "DEEPSEEK_V3"
                    }

        # Tier 1: Local Regex Compactor
        files = cls.FILE_REGEX.findall(raw_text)
        target_file = files[0] if files else ""

        action = "TASK"
        for act_name, regex in cls.ACTION_PATTERNS:
            if regex.search(raw_text):
                action = act_name
                break

        traits = []
        for regex, tag in cls.TRAIT_MAPPINGS:
            if regex.search(raw_text):
                if tag not in traits:
                    traits.append(tag)

        symbol = ""
        m_sym = cls.SYMBOL_REGEX_KEYWORD.search(raw_text)
        if m_sym:
            symbol = m_sym.group(1)
        elif not symbol:
            m_backtick = cls.SYMBOL_REGEX_BACKTICK.search(raw_text)
            if m_backtick and m_backtick.group(1) != target_file:
                symbol = m_backtick.group(1)
        elif not symbol:
            m_call = cls.SYMBOL_REGEX_CALL.search(raw_text)
            if m_call and m_call.group(1) != target_file:
                symbol = m_call.group(1)

        delta = raw_text
        if target_file:
            delta = delta.replace(target_file, " ")
        if symbol:
            delta = re.sub(r"\b" + re.escape(symbol) + r"\b", " ", delta)
            delta = delta.replace(f"`{symbol}`", " ")

        for pattern in cls.STOP_PHRASES:
            delta = re.sub(pattern, " ", delta, flags=re.I)

        for _, regex in cls.ACTION_PATTERNS:
            delta = regex.sub(" ", delta)

        for regex, _ in cls.TRAIT_MAPPINGS:
            delta = regex.sub(" ", delta)

        delta = re.sub(r"[,\.:;\"'\(\)\[\]\{\}`\?]", " ", delta)
        delta = re.sub(r"\s+", " ", delta).strip()

        parts = [action]
        if target_file:
            parts.append(target_file)
        if symbol:
            parts.append(symbol)
        if traits:
            parts.append(f"[{' '.join(traits)}]")
        if delta and len(delta) > 2:
            parts.append(f'"{delta}"')

        compact_expr = f"({' '.join(parts)})"
        compact_tokens = count_tokens(compact_expr)

        if compact_tokens > original_tokens:
            compact_expr = raw_text.strip()
            compact_tokens = original_tokens

        saved = max(0, original_tokens - compact_tokens)
        pct = (saved / original_tokens * 100.0) if original_tokens > 0 else 0.0

        return {
            "original_text": raw_text,
            "compact_text": compact_expr,
            "original_tokens": original_tokens,
            "compact_tokens": compact_tokens,
            "saved_tokens": saved,
            "reduction_pct": round(pct, 1),
            "action": action,
            "target_file": target_file,
            "symbol": symbol,
            "traits": traits,
            "tier": "REGEX"
        }
