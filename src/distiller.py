"""
Universal Multi-Provider Intent Distiller:
Translates verbose, conversational developer prompts into ultra-dense TOCP S-Expressions.
Supports DeepSeek-V3, OpenAI ChatGPT (gpt-4o-mini), and Google Gemini (2.0 Flash / 1.5 Flash).
Implemented with pure standard-library urllib (zero dependencies, strict timeout).
"""

import os
import json
import re
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple
from src.distiller_cache import DistillationCache

class UniversalDistiller:
    """Multi-provider semantic intent compiler supporting DeepSeek, OpenAI, and Google Gemini."""

    DEFAULT_TIMEOUT = 3.5

    SYSTEM_PROMPT = """You are a semantic code intent compiler for the Token-Optimized Communication Protocol (TOCP). Your task is to compress verbose, conversational developer requests into an ultra-dense S-Expression with this exact syntax:
(ACTION [filepath] [symbol] [[traits...]] ["minimal_intent"])

Available ACTIONS: REFACTOR, DEBUG, CREATE, OPTIMIZE, EXPLAIN, AUDIT, TEST, RUN.

Examples:
Input: "Слушай, перепиши пожалуйста функцию get_balance в файле wallet.ts, добавь туда redis cache и сделай её асинхронной"
Output: (REFACTOR wallet.ts get_balance [async redis] "add caching and async")

Input: "У меня в auth.controller.ts при логине через гугл если нет аватарки промис зависает на 30 сек и падает 504"
Output: (DEBUG auth.controller.ts [oauth google timeout_504 missing_avatar] "fix 30s hang on no avatar")

Rules:
1. Output ONLY the S-Expression starting with ( and ending with ).
2. No markdown code blocks, no preamble, no explanations.
3. Keep minimal_intent ultra-compact (under 10 words, English preferred for BPE token economy). Total response MUST be under 50 tokens."""

    PROVIDERS = {
        "deepseek": {
            "name": "DeepSeek-Flash",
            "env_key": "DEEPSEEK_API_KEY",
            "config_key": "deepseek_api_key",
            "default_model": "deepseek-flash",
            "endpoint": "https://api.deepseek.com/chat/completions"
        },
        "openai": {
            "name": "OpenAI ChatGPT",
            "env_key": "OPENAI_API_KEY",
            "config_key": "openai_api_key",
            "default_model": "gpt-4o-mini",
            "endpoint": "https://api.openai.com/v1/chat/completions"
        },
        "gemini": {
            "name": "Google Gemini",
            "env_key": "GEMINI_API_KEY",
            "config_key": "gemini_api_key",
            "default_model": "gemini-2.0-flash-lite",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        }
    }

    @classmethod
    def get_configured_providers(cls) -> Dict[str, Dict[str, Any]]:
        """Scans environment and data/config.json for configured LLM distillation keys."""
        config_file_data = {}
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "data", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_file_data = json.load(f)
            except Exception:
                pass

        available = {}
        preferred = config_file_data.get("preferred_provider")

        for key, p_info in cls.PROVIDERS.items():
            api_key = os.environ.get(p_info["env_key"], "").strip()
            if not api_key and p_info["config_key"] in config_file_data:
                api_key = str(config_file_data[p_info["config_key"]]).strip()

            if api_key:
                model = config_file_data.get(f"{key}_model", p_info["default_model"])
                timeout = float(config_file_data.get(f"{key}_timeout", cls.DEFAULT_TIMEOUT))
                max_tokens = int(config_file_data.get("distill_max_tokens", 60))
                available[key] = {
                    "provider": key,
                    "name": p_info["name"],
                    "api_key": api_key,
                    "model": model,
                    "timeout": timeout,
                    "max_tokens": max_tokens,
                    "endpoint": p_info["endpoint"]
                }

        return available

    @classmethod
    def distill(cls, raw_text: str, preferred_provider: Optional[str] = None) -> Optional[str]:
        """
        Distills raw conversational text into an S-Expression using the best available provider.
        Priority: Explicit preferred -> Gemini -> OpenAI -> DeepSeek.
        """
        if not raw_text or len(raw_text.strip()) < 10:
            return None

        # Check distillation cache before remote API roundtrips
        cached = DistillationCache.get(raw_text)
        if cached:
            return cached

        providers = cls.get_configured_providers()
        if not providers:
            return None

        # Determine order
        order = []
        if preferred_provider and preferred_provider in providers:
            order.append(preferred_provider)
        # Add remaining in priority order
        for p in ["gemini", "openai", "deepseek"]:
            if p in providers and p not in order:
                order.append(p)

        for p_key in order:
            p_conf = providers[p_key]
            try:
                result = None
                if p_key in ("deepseek", "openai"):
                    result = cls._call_openai_compatible(p_conf, raw_text)
                elif p_key == "gemini":
                    result = cls._call_gemini(p_conf, raw_text)

                if result:
                    clean = cls._sanitize_s_expression(result)
                    if clean:
                        DistillationCache.set(raw_text, clean, provider=p_key)
                        return clean
            except Exception:
                continue

        return None

    @classmethod
    def _call_openai_compatible(cls, conf: Dict[str, Any], raw_text: str) -> Optional[str]:
        payload = {
            "model": conf["model"],
            "messages": [
                {"role": "system", "content": cls.SYSTEM_PROMPT},
                {"role": "user", "content": raw_text.strip()}
            ],
            "temperature": 0.0,
            "max_tokens": conf.get("max_tokens", 60)
        }
        if conf.get("provider") == "deepseek" or "deepseek" in str(conf.get("endpoint", "")).lower():
            payload["effort"] = "low"

        req = urllib.request.Request(
            conf["endpoint"],
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {conf['api_key']}",
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe/1.0"
            }
        )

        with urllib.request.urlopen(req, timeout=conf["timeout"]) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        return None

    @classmethod
    def _call_gemini(cls, conf: Dict[str, Any], raw_text: str) -> Optional[str]:
        endpoint = conf["endpoint"].format(model=conf["model"], key=conf["api_key"])
        payload = {
            "systemInstruction": {
                "parts": [{"text": cls.SYSTEM_PROMPT}]
            },
            "contents": [
                {"parts": [{"text": raw_text.strip()}]}
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": conf.get("max_tokens", 60)
            }
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe/1.0"
            }
        )

        with urllib.request.urlopen(req, timeout=conf["timeout"]) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
        return None

    @classmethod
    def _sanitize_s_expression(cls, content: str) -> Optional[str]:
        content = content.strip()
        # Remove markdown fences if model hallucinated backticks
        if content.startswith("```"):
            lines = content.splitlines()
            content = " ".join([l for l in lines if not l.startswith("```")]).strip()

        # Extract parenthesized S-Expression
        match = re.search(r"\([A-Z]+[^\)]*\)", content)
        if match:
            return match.group(0).strip()
        elif content.startswith("(") and content.endswith(")"):
            return content
        return None
