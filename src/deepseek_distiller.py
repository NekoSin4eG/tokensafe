"""
DeepSeek-Flash Intent Distiller: Translates complex, conversational developer requests
into ultra-dense Token-Optimized Communication Protocol (TOCP) S-Expressions.
Uses native standard-library urllib with strict timeout and zero dependencies.
"""

import os
import json
import re
import urllib.request
import urllib.error
from typing import Optional, Dict, Any
from src.distiller_cache import DistillationCache
from src.license_manager import LicenseManager

class DeepSeekDistiller:
    """Uses DeepSeek-Flash as a high-density semantic compiler for prompts."""

    DEFAULT_URL = "https://api.deepseek.com/chat/completions"
    DEFAULT_MODEL = "deepseek-flash"
    DEFAULT_TIMEOUT = 3.5

    SYSTEM_PROMPT = """You are a multilingual semantic code intent compiler for the Token-Optimized Communication Protocol (TOCP). Your task is to compress verbose, conversational developer requests into an ultra-dense S-Expression with this exact syntax:
(ACTION [filepath] [symbol] [[traits...]] ["minimal_intent"])

Available ACTIONS: REFACTOR, DEBUG, CREATE, OPTIMIZE, EXPLAIN, AUDIT, TEST, RUN.

Examples:
Input: "Could you please rewrite the get_balance function in wallet.ts, add redis caching to it and make it async?"
Output: (REFACTOR wallet.ts get_balance [async redis] "add caching and async")

Input: "In auth.controller.ts during Google login, if user has no avatar the promise hangs for 30s and throws 504"
Output: (DEBUG auth.controller.ts [oauth google timeout_504 missing_avatar] "fix 30s hang on no avatar")

Input: "Por favor arregla la función validate_token en auth.py para que verifique jwt y devuelva boolean"
Output: (DEBUG auth.py validate_token [jwt] "verify jwt and return boolean")

Rules:
1. Output ONLY the S-Expression starting with ( and ending with ).
2. No markdown code blocks, no preamble, no explanations.
3. Keep minimal_intent ultra-compact (under 10 words, English preferred for BPE token economy). Total response MUST be under 50 tokens."""

    @classmethod
    def _get_config(cls) -> Dict[str, Any]:
        """Loads API key and configuration from environment or data/config.json."""
        config = {
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "model": cls.DEFAULT_MODEL,
            "timeout": cls.DEFAULT_TIMEOUT,
            "enabled": True
        }

        # Check data/config.json relative to project root
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "data", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_conf = json.load(f)
                    if not config["api_key"] and file_conf.get("deepseek_api_key"):
                        config["api_key"] = file_conf["deepseek_api_key"]
                    config["model"] = file_conf.get("deepseek_model", config["model"])
                    config["timeout"] = file_conf.get("deepseek_timeout", config["timeout"])
                    config["max_tokens"] = int(file_conf.get("distill_max_tokens", 60))
                    config["enabled"] = file_conf.get("enable_deepseek_tier", True)
            except Exception:
                pass

        return config

    @classmethod
    def distill(cls, raw_text: str) -> Optional[str]:
        """
        Sends raw text to DeepSeek-V3 to compile into an S-Expression.
        Returns distilled S-Expression or None if unavailable/timed out.
        Requires active TokenSafe Solo or Team subscription.
        """
        tier, _ = LicenseManager.get_active_license()
        if tier == "free":
            return None

        # In standard SaaS mode, delegate to UniversalDistiller's Managed Cloud Distillation
        if os.environ.get("TOKENSAFE_SELF_HOSTED", "").lower() not in ("1", "true", "yes"):
            from src.distiller import UniversalDistiller
            return UniversalDistiller.distill(raw_text)

        conf = cls._get_config()
        if not conf["enabled"] or not conf["api_key"] or not raw_text or len(raw_text.strip()) < 10:
            return None

        # Check distillation cache before remote roundtrips
        cached = DistillationCache.get(raw_text)
        if cached:
            return cached

        payload = {
            "model": conf["model"],
            "messages": [
                {"role": "system", "content": cls.SYSTEM_PROMPT},
                {"role": "user", "content": raw_text.strip()}
            ],
            "temperature": 0.0,
            "max_tokens": conf.get("max_tokens", 60),
            "effort": "low"
        }

        req = urllib.request.Request(
            cls.DEFAULT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {conf['api_key']}",
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=conf["timeout"]) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "").strip()
                        # Clean backticks if any
                        content = re.sub(r"^```[a-zA-Z]*\n?|```$", "", content).strip()
                        if content.startswith("(") and content.endswith(")"):
                            DistillationCache.set(raw_text, content, provider="deepseek")
                            return content
        except Exception:
            # Network issue, timeout, or rate limit: fallback gracefully
            return None

        return None
