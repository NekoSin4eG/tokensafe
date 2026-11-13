"""
Managed-Cloud-First Intent Distiller:
Translates verbose, conversational developer prompts into ultra-dense TOCP S-Expressions.
Paid tiers use the TokenSafe Cloud Distiller out of the box (zero API keys, license-key auth).
Self-hosted/Enterprise optionally supports BYOK: DeepSeek, OpenAI ChatGPT, and Google Gemini.
Implemented with pure standard-library urllib (zero dependencies, strict timeout).
"""

import os
import json
import re
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple
from src.distiller_cache import DistillationCache
from src.license_manager import LicenseManager
from src.paths import resolve_data_dir

class UniversalDistiller:
    """Managed-cloud-first semantic intent compiler: TokenSafe Cloud (zero keys) with optional BYOK DeepSeek, OpenAI, and Google Gemini."""

    DEFAULT_TIMEOUT = 2.5
    # After a failed roundtrip, skip all remote distillation for this long.
    # distill() runs synchronously inside the PreInvocation lifecycle hook, so a
    # dead/slow provider must not add seconds of latency to every single prompt.
    FAILURE_COOLDOWN_SECONDS = 180.0

    @classmethod
    def _failure_state_path(cls) -> str:
        return os.path.join(resolve_data_dir(), "distill_failure_state.json")

    @classmethod
    def _cooldown_active(cls) -> bool:
        """True when a recent provider failure put distillation into cooldown."""
        try:
            with open(cls._failure_state_path(), "r", encoding="utf-8") as f:
                state = json.load(f)
            return (time.time() - float(state.get("last_failure_ts", 0.0))) < cls.FAILURE_COOLDOWN_SECONDS
        except Exception:
            return False

    @classmethod
    def _record_failure(cls):
        try:
            path = cls._failure_state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"last_failure_ts": time.time()}, f)
        except Exception:
            pass

    @classmethod
    def _clear_failure(cls):
        try:
            path = cls._failure_state_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

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
            "default_model": "gemini-flash-lite-latest",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        }
    }

    @classmethod
    def is_self_hosted(cls) -> bool:
        """Determines if running in Docker self-hosted mode with custom BYOK keys."""
        return os.environ.get("TOKENSAFE_SELF_HOSTED", "").lower() in ("1", "true", "yes")

    @classmethod
    def _call_cloud_distill(cls, raw_text: str, lic_key: str, mode: str = "") -> Optional[str]:
        cloud_url = os.environ.get("TOKENSAFE_CLOUD_URL", "https://api.tokensafe.dev").rstrip("/")
        endpoint = f"{cloud_url}/api/distill"
        payload = {"prompt": raw_text.strip()}
        if mode:
            payload["mode"] = mode
        timeout = cls.HISTORY_DISTILL_TIMEOUT if mode == "history" else cls.DEFAULT_TIMEOUT
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {lic_key}",
                "Content-Type": "application/json",
                "User-Agent": "TokenSafe/1.3.7"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("success") and data.get("distilled"):
                        return data["distilled"].strip()
        except Exception:
            return None
        return None

    HISTORY_DISTILL_TIMEOUT = 8.0

    @classmethod
    def distill_history(cls, raw_text: str) -> Optional[str]:
        """
        Managed-cloud history summarization for the Tool Result Ledger (v3.0.0):
        compresses an aged tool-result segment into a terse state ledger.
        Paid license required (free tier = hard-off per the distiller contract);
        managed cloud only — self-hosted BYOK keeps the mechanical fold.
        Shares the failure-cooldown breaker with prompt distillation.
        """
        if not raw_text or len(raw_text.strip()) < 50:
            return None
        if cls._cooldown_active():
            return None
        cache_key_text = "HIST:" + raw_text[:4000]
        cached = DistillationCache.get(cache_key_text)
        if cached:
            return cached
        tier, _ = LicenseManager.get_active_license()
        if tier == "free":
            return None
        providers = cls.get_configured_providers()
        if "cloud" not in providers:
            return None  # self-hosted BYOK: mechanical fold stays the contract
        lic_key = LicenseManager.get_active_license_key()
        if not lic_key:
            return None
        cloud_res = cls._call_cloud_distill(raw_text, lic_key, mode="history")
        if cloud_res:
            DistillationCache.set(cache_key_text, cloud_res, provider="cloud-managed")
            cls._clear_failure()
            return cloud_res
        cls._record_failure()
        return None

    @classmethod
    def get_configured_providers(cls) -> Dict[str, Dict[str, Any]]:
        """Scans environment and data/config.json for configured LLM distillation keys."""
        tier, _ = LicenseManager.get_active_license()
        if tier == "free":
            return {}

        if not cls.is_self_hosted():
            return {
                "cloud": {
                    "provider": "cloud",
                    "name": "TokenSafe Cloud Distiller",
                    "model": "gemini-flash-lite-latest",
                    "status": "managed"
                }
            }

        config_file_data = {}
        config_path = os.path.join(resolve_data_dir(), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    loaded_cfg = json.load(f)
                if isinstance(loaded_cfg, dict):
                    config_file_data = loaded_cfg
            except Exception:
                pass

        available = {}
        preferred = config_file_data.get("preferred_provider")

        for key, p_info in cls.PROVIDERS.items():
            api_key = os.environ.get(p_info["env_key"], "").strip()
            if not api_key and p_info["config_key"] in config_file_data:
                api_key = str(config_file_data[p_info["config_key"]] or "").strip()

            if api_key:
                model = str(config_file_data.get(f"{key}_model") or p_info["default_model"])
                try:
                    raw_to = config_file_data.get(f"{key}_timeout")
                    timeout = float(raw_to if raw_to is not None else cls.DEFAULT_TIMEOUT)
                except (TypeError, ValueError):
                    timeout = cls.DEFAULT_TIMEOUT
                try:
                    raw_mt = config_file_data.get("distill_max_tokens")
                    max_tokens = int(raw_mt if raw_mt is not None else 60)
                except (TypeError, ValueError):
                    max_tokens = 60
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
        Priority: Managed Cloud (license-auth, zero keys) -> Explicit preferred -> Gemini -> OpenAI -> DeepSeek.
        Requires active TokenSafe Solo or Team subscription.
        """
        tier, _ = LicenseManager.get_active_license()
        if tier == "free":
            return None

        if not raw_text or len(raw_text.strip()) < 10:
            return None

        # Check distillation cache before remote API roundtrips
        cached = DistillationCache.get(raw_text)
        if cached:
            return cached

        # Circuit breaker: a recent provider failure suspends remote roundtrips so a
        # dead endpoint cannot add seconds of latency to every prompt invocation.
        if cls._cooldown_active():
            return None

        providers = cls.get_configured_providers()
        if not providers:
            return None

        # 1. Managed Cloud Distillation (Default for Solo/Team SaaS)
        if "cloud" in providers:
            lic_key = LicenseManager.get_active_license_key()

            if lic_key:
                cloud_res = cls._call_cloud_distill(raw_text, lic_key)
                if cloud_res:
                    clean = cls._sanitize_s_expression(cloud_res)
                    if clean:
                        DistillationCache.set(raw_text, clean, provider="cloud-managed")
                        cls._clear_failure()
                        return clean
                # Failure (network/timeout/invalid shape): trip the cooldown breaker
                cls._record_failure()
            return None

        # 2. Self-Hosted Docker Mode (BYOK local providers)
        # Determine order
        pref = preferred_provider
        if not pref:
            config_path = os.path.join(resolve_data_dir(), "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        pref = json.load(f).get("preferred_provider")
                except Exception:
                    pass

        order = []
        if pref and pref in providers:
            order.append(pref)
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
                        cls._clear_failure()
                        return clean
            except Exception:
                continue

        # All providers failed: trip the cooldown breaker
        cls._record_failure()
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

        # Balanced quote-aware extraction so parens inside "intent foo(x)" don't truncate
        m_start = re.search(r"\([A-Z]+(?:\s|\))", content)
        if m_start:
            start = m_start.start()
            depth = 0
            in_quote = False
            i = start
            while i < len(content):
                ch = content[i]
                if in_quote:
                    if ch == "\\" and i + 1 < len(content):
                        i += 2
                        continue
                    if ch == '"':
                        in_quote = False
                else:
                    if ch == '"':
                        in_quote = True
                    elif ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            return content[start : i + 1].strip()
                i += 1
        if content.startswith("(") and content.endswith(")"):
            return content
        return None
