"""
Semantic Compactor: Converts verbose natural language prompts (Russian/English)
into ultra-dense Token-Optimized Communication Protocol (TOCP) S-Expressions.
Supports 3-Tier Routing: Pass-through -> Regex Compactor -> DeepSeek-V3 Distiller.
"""

import re
from typing import Dict, List, Optional
from src.tokenizer import count_tokens
from src.distiller import UniversalDistiller

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
    def compact(cls, raw_text: str, enable_deepseek: bool = True) -> Dict[str, any]:
        """
        Compresses input prompt via 3-Tier Architecture:
        - Tier 0: Pass-through for very short inputs (< 6 tokens)
        - Tier 2: DeepSeek-V3 Distiller for complex/long inputs (> 15 tokens)
        - Tier 1: Local Regex Compactor (fallback or structured commands)
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
        if enable_deepseek and original_tokens >= 15:
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
        symbol_match = re.search(
            r"(?:функци[юяие]|function|method|метод|class|класс|handler|хэндлер|symbol|переменн[уюая]+)\s+[`'\"]?([a-zA-Z0-9_]+)[`'\"]?",
            raw_text,
            re.I
        )
        if symbol_match:
            symbol = symbol_match.group(1)

        delta = raw_text
        if target_file:
            delta = delta.replace(target_file, " ")
        if symbol:
            delta = re.sub(r"\b" + re.escape(symbol) + r"\b", " ", delta)

        for pattern in cls.STOP_PHRASES:
            delta = re.sub(pattern, " ", delta, flags=re.I)

        for _, regex in cls.ACTION_PATTERNS:
            delta = regex.sub(" ", delta)

        for regex, _ in cls.TRAIT_MAPPINGS:
            delta = regex.sub(" ", delta)

        delta = re.sub(r"[,\.:;\"'\(\)\[\]\{\}]", " ", delta)
        delta = re.sub(r"\s+", " ", delta).strip()

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

        if compact_tokens >= original_tokens:
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
