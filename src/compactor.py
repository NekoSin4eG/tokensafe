"""
Semantic Compactor: Converts verbose natural language prompts (Russian/English)
into ultra-dense Token-Optimized Communication Protocol (TOCP) S-Expressions.
Strips politeness fluff, extracts code targets, symbols, and constraints.
"""

import re
from typing import Dict, List, Optional
from src.tokenizer import count_tokens

class PromptCompactor:
    """Transforms verbose natural language requests into compact Intent IR."""

    STOP_PHRASES = [
        # Russian courtesy, prepositions & fillers
        r"\b(пожалуйста|будь добр|будь любезен|если не трудно|заранее спасибо|спасибо|благодарю)\b",
        r"\b(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|хай|салют)\b",
        r"\b(слушай|смотри|короче|кстати|в общем|в общем-то|дело в том что)\b",
        r"\b(можешь ли ты|можешь|не мог бы ты|помоги мне|помоги пожалуйста|мне нужно|я хочу чтобы ты|я хочу|нужно чтобы)\b",
        r"\b(подскажи как|подскажи пожалуйста|расскажи как|сделай так чтобы|сделай пожалуйста|попробуй)\b",
        r"\b(как думаешь|как считаешь|по твоему мнению)\b",
        r"\b(в файле|из файла|внутри файла|функцию|метод|класс|код|строку)\b",
        r"\b(добавь туда|сделай её|сделай его|чтобы было|для этого)\b",
        r"\b(и|в|на|с|при|по|к|из|у|о|об|от|под|над|для)\b",
        
        # English courtesy, prepositions & fillers
        r"\b(please|kindly|could you please|could you|would you mind|would you|thank you|thanks in advance)\b",
        r"\b(hello|hi there|hi|hey|good morning|good evening|good afternoon)\b",
        r"\b(listen|look|basically|anyway|actually|to be honest)\b",
        r"\b(can you help me with|can you help me|can you|help me to|help me|i want you to|i would like you to|i need to|i want to)\b",
        r"\b(make sure to|make sure that|tell me how to|show me how to)\b",
        r"\b(in file|inside file|the function|the method|the class)\b",
        r"\b(add to it|make it|so that it)\b",
        r"\b(and|with|on|at|for|from|to|the|a|an|in|of)\b",
    ]

    ACTION_PATTERNS = [
        ("REFACTOR", re.compile(r"\b(refactor|rewrite|restructure|перепиши|отрефактори|отрефакторить|переделай|модернизируй)\b", re.I)),
        ("DEBUG", re.compile(r"\b(fix|debug|resolve|error|bug|issue|broken|failing|crash|почини|исправь|баг|ошибка|ошибку|не работает|падает|падение)\b", re.I)),
        ("OPTIMIZE", re.compile(r"\b(optimize|speed up|perf|memory leak|latency|ускори|оптимизируй|утечка памяти|быстрее)\b", re.I)),
        ("CREATE", re.compile(r"\b(create|add|implement|build|write|new|generate|создай|добавь|напиши|реализуй|сделай|напиши новый)\b", re.I)),
        ("TEST", re.compile(r"\b(test|spec|assert|coverage|unit test|тест|тесты|протестируй|покрыть тестами)\b", re.I)),
        ("EXPLAIN", re.compile(r"\b(explain|why|how does|what is|clarify|объясни|почему|как работает|что делает|поясни)\b", re.I)),
        ("AUDIT", re.compile(r"\b(audit|review|check|security|analyze|проверь|проанализируй|аудит|ревью)\b", re.I)),
        ("RUN", re.compile(r"\b(run|execute|start|launch|запусти|выполни|стартуй)\b", re.I)),
    ]

    FILE_REGEX = re.compile(r"[\w\-\./\\]+\.(?:ts|js|tsx|jsx|py|go|rs|java|c|cpp|h|json|yaml|yml|md|proto|toml|sql|html|css|sh)", re.I)

    # Constraint patterns with multilingual mapping
    TRAIT_MAPPINGS = [
        (re.compile(r"\b(async|асинхронн\w*)\b", re.I), "async"),
        (re.compile(r"\b(sync|синхронн\w*)\b", re.I), "sync"),
        (re.compile(r"\b(redis|редис)\b", re.I), "redis"),
        (re.compile(r"\b(cache|кэш|кеш|кэширован\w*|кеширован\w*)\b", re.I), "cache"),
        (re.compile(r"\b(postgres|postgresql|постгрес)\b", re.I), "postgres"),
        (re.compile(r"\b(sqlite)\b", re.I), "sqlite"),
        (re.compile(r"\b(prisma|призма)\b", re.I), "prisma"),
        (re.compile(r"\b(docker|докер)\b", re.I), "docker"),
        (re.compile(r"\b(jwt|токен\w*|auth|авторизац\w*)\b", re.I), "jwt"),
        (re.compile(r"\b(zero_alloc|без аллокаций)\b", re.I), "zero_alloc"),
        (re.compile(r"\b(typesafe|типизац\w*|типобезопасн\w*)\b", re.I), "typesafe"),
        (re.compile(r"\b(tailwind)\b", re.I), "tailwind"),
        (re.compile(r"\b(unit_test|юнит\w*)\b", re.I), "unit_test"),
    ]

    @classmethod
    def compact(cls, raw_text: str) -> Dict[str, any]:
        """
        Compresses input prompt and returns structured details + metrics.
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
            }

        original_tokens = count_tokens(raw_text)

        # 1. Target file
        files = cls.FILE_REGEX.findall(raw_text)
        target_file = files[0] if files else ""

        # 2. Action
        action = "TASK"
        for act_name, regex in cls.ACTION_PATTERNS:
            if regex.search(raw_text):
                action = act_name
                break

        # 3. Traits
        traits = []
        for regex, tag in cls.TRAIT_MAPPINGS:
            if regex.search(raw_text):
                if tag not in traits:
                    traits.append(tag)

        # 4. Symbol
        symbol = ""
        symbol_match = re.search(
            r"(?:функци[юяие]|function|method|метод|class|класс|handler|хэндлер|symbol|переменн[уюая]+)\s+[`'\"]?([a-zA-Z0-9_]+)[`'\"]?",
            raw_text,
            re.I
        )
        if symbol_match:
            symbol = symbol_match.group(1)

        # 5. Distill residual delta
        delta = raw_text
        if target_file:
            delta = delta.replace(target_file, " ")
        if symbol:
            delta = re.sub(r"\b" + re.escape(symbol) + r"\b", " ", delta)

        # Remove stop phrases
        for pattern in cls.STOP_PHRASES:
            delta = re.sub(pattern, " ", delta, flags=re.I)

        # Remove action words from delta
        for _, regex in cls.ACTION_PATTERNS:
            delta = regex.sub(" ", delta)

        # Remove traits matched words from delta
        for regex, _ in cls.TRAIT_MAPPINGS:
            delta = regex.sub(" ", delta)

        # Clean punctuation and whitespace
        delta = re.sub(r"[,\.:;\"'\(\)\[\]\{\}]", " ", delta)
        delta = re.sub(r"\s+", " ", delta).strip()

        # 6. Build Lean S-Expression
        parts = [f"({action}"]
        if target_file:
            parts.append(target_file)
        if symbol:
            parts.append(symbol)
        if traits:
            parts.append(f"[{' '.join(traits)}]")
        if delta and len(delta) > 2:
            parts.append(f'"{delta}"')
        parts.append(")")

        compact_expr = " ".join(parts)
        compact_tokens = count_tokens(compact_expr)

        # Safety check: if prompt was already super short (< 6 tokens) or compact is not shorter
        if original_tokens < 6 or compact_tokens >= original_tokens:
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
        }
