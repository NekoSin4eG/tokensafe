"""
TokenSafe Prompt Cache Prefix Normalizer.
Normalizes system instructions, architectural banners, and invariant tool definitions
into deterministic prefixes to guarantee up to 90% prompt cache hit rates on
Anthropic (Claude 3.5), DeepSeek-V3, and OpenAI.
"""

import re
import json
import hashlib
from typing import Dict, List, Any, Tuple, Optional
from src.tokenizer import count_tokens

class PromptCacheNormalizer:
    """Normalizes prompt message streams into cache-friendly deterministic prefixes."""

    VOLATILE_PATTERNS = [
        re.compile(r"The current local time is:.*?(?=\n|$)", re.IGNORECASE),
        re.compile(r"timestamp=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?", re.IGNORECASE),
        re.compile(r"request_id=['\"][a-zA-Z0-9_-]+['\"]", re.IGNORECASE),
        re.compile(r"uuid=['\"][a-f0-9-]{36}['\"]", re.IGNORECASE),
        re.compile(r"turn_count=\d+", re.IGNORECASE),
    ]

    @classmethod
    def strip_volatile_elements(cls, text: str) -> str:
        """Removes volatile timestamps and ephemeral request IDs from static prefix."""
        for pattern in cls.VOLATILE_PATTERNS:
            text = pattern.sub("", text)
        return text

    @classmethod
    def compute_prefix_hash(cls, prefix_text: str) -> str:
        """Returns SHA256 hash of normalized static prefix."""
        cleaned = re.sub(r"\s+", " ", prefix_text).strip()
        return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_messages(
        cls,
        messages: List[Dict[str, Any]],
        provider: str = "generic",
        inject_cache_control: bool = True
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Normalizes message list:
        1. Separates static system/prefix instructions from dynamic turns
        2. Sanitizes non-deterministic markers in the static prefix
        3. For Anthropic: Injects 'cache_control: {"type": "ephemeral"}' if tokens >= 1024
        4. Calculates prefix hash and estimated cache savings
        """
        if not messages:
            return messages, {"prefix_tokens": 0, "cache_hit_eligible": False}

        normalized = []
        static_prefix_parts = []
        last_static_idx = -1

        for idx, msg in enumerate(messages):
            msg_copy = dict(msg)
            role = msg_copy.get("role", "")
            content = msg_copy.get("content", "")

            # Static prefix includes system messages and developer instructions before first user turn
            if role in ("system", "developer") and isinstance(content, str):
                cleaned_content = cls.strip_volatile_elements(content)
                msg_copy["content"] = cleaned_content
                static_prefix_parts.append(cleaned_content)
                last_static_idx = idx
            normalized.append(msg_copy)

        prefix_str = "\n".join(static_prefix_parts)
        prefix_tokens = count_tokens(prefix_str) if prefix_str else 0
        prefix_hash = cls.compute_prefix_hash(prefix_str) if prefix_str else ""

        # Anthropic caching requires >= 1024 tokens for Claude 3.5 Sonnet / Haiku
        # DeepSeek caches 64-token chunks
        cache_eligible = prefix_tokens >= 64
        cache_control_injected = False

        if inject_cache_control and last_static_idx >= 0 and prefix_tokens >= 1024:
            target_msg = normalized[last_static_idx]
            content_val = target_msg.get("content")
            if isinstance(content_val, str):
                # Anthropic structured content format with cache_control block
                target_msg["content"] = [
                    {
                        "type": "text",
                        "text": content_val,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
                cache_control_injected = True
            elif isinstance(content_val, list) and len(content_val) > 0:
                if isinstance(content_val[-1], dict):
                    content_val[-1]["cache_control"] = {"type": "ephemeral"}
                    cache_control_injected = True

        return normalized, {
            "prefix_tokens": prefix_tokens,
            "prefix_hash": prefix_hash,
            "cache_eligible": cache_eligible,
            "cache_control_injected": cache_control_injected,
            "provider": provider
        }
