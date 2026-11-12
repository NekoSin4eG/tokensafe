"""
TokenSafe Prompt Cache Prefix Normalizer.
Normalizes system instructions, tool schemas, architectural banners, and stable
history checkpoints into deterministic prefixes with multi-breakpoint cache_control
to maximize prompt cache hit rates on Anthropic, DeepSeek, OpenAI, and Gemini.
"""

import re
import json
import hashlib
from typing import Dict, List, Any, Tuple, Optional
from src.tokenizer import count_tokens


def _canonicalize_json(obj: Any) -> Any:
    """Recursively sorts dictionary keys so serialized tool schemas are byte-deterministic."""
    if isinstance(obj, dict):
        return {k: _canonicalize_json(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_canonicalize_json(item) for item in obj]
    return obj


def _tool_name(tool: Dict[str, Any]) -> str:
    if not isinstance(tool, dict):
        return ""
    if tool.get("name"):
        return str(tool["name"])
    fn = tool.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn["name"])
    return ""


def _inject_cache_control_on_msg(msg: Dict[str, Any]) -> bool:
    """Attaches Anthropic cache_control to the last non-empty block of a message."""
    content_val = msg.get("content")
    if isinstance(content_val, str):
        if not content_val.strip():
            return False
        msg["content"] = [
            {
                "type": "text",
                "text": content_val,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return True
    if isinstance(content_val, list) and content_val:
        copied = [dict(b) if isinstance(b, dict) else b for b in content_val]
        for block in reversed(copied):
            if isinstance(block, dict):
                btype = block.get("type")
                if btype in ("thinking", "redacted_thinking"):
                    continue
                if btype == "text" and not str(block.get("text", "")).strip():
                    continue
                block["cache_control"] = {"type": "ephemeral"}
                msg["content"] = copied
                return True
    return False


def _count_existing_cache_controls(tools: Optional[List[Any]], messages: List[Dict[str, Any]]) -> int:
    count = 0
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict) and "cache_control" in t:
                count += 1
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and "cache_control" in b:
                    count += 1
    return count


def _message_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif b.get("type") == "tool_use" and b.get("input") is not None:
                parts.append(json.dumps(b["input"], ensure_ascii=False))
            elif b.get("type") == "tool_result":
                tc = b.get("content")
                if isinstance(tc, str):
                    parts.append(tc)
                elif tc is not None:
                    parts.append(json.dumps(tc, ensure_ascii=False))
        return "\n".join(parts)
    return ""


class PromptCacheNormalizer:
    """Normalizes prompt message streams and tool definitions into cache-friendly prefixes."""

    VOLATILE_PATTERNS = [
        re.compile(r"The current local time is:.*?(?=\n|$)", re.IGNORECASE),
        re.compile(r"timestamp=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?", re.IGNORECASE),
        re.compile(r"request_id=['\"][a-zA-Z0-9_-]+['\"]", re.IGNORECASE),
        re.compile(r"uuid=['\"][a-f0-9-]{36}['\"]", re.IGNORECASE),
        re.compile(r"turn_count=\d+", re.IGNORECASE),
        re.compile(r"\(Dedup Hit #\d+, Saved ~?\d[\d,]* tokens\)", re.IGNORECASE),
        re.compile(r"Saved \*?\*?\d[\d,]* tokens\*?\*?", re.IGNORECASE),
        re.compile(r"Conversation ID:\s*[a-f0-9-]{8,36}", re.IGNORECASE),
        re.compile(r"invocationNum[\"']?\s*[:=]\s*\d+", re.IGNORECASE),
        re.compile(r"Generated locally at \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", re.IGNORECASE),
    ]

    @classmethod
    def strip_volatile_elements(cls, text: str) -> str:
        """Removes volatile timestamps, hit counters, and ephemeral IDs from static prefix."""
        for pattern in cls.VOLATILE_PATTERNS:
            text = pattern.sub("", text)
        return text

    @classmethod
    def compute_prefix_hash(cls, prefix_text: str) -> str:
        """Returns SHA256 hash of normalized static prefix."""
        cleaned = re.sub(r"\s+", " ", prefix_text).strip()
        return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_tools(
        cls,
        tools: Optional[List[Dict[str, Any]]],
        provider: str = "generic",
        inject_cache_control: bool = True,
    ) -> Optional[List[Dict[str, Any]]]:
        """Sorts tools by name and canonicalizes JSON key order for byte-identical prefixes."""
        if not tools or not isinstance(tools, list):
            return tools
        canon = [_canonicalize_json(dict(t)) if isinstance(t, dict) else t for t in tools]
        canon.sort(key=_tool_name)
        if inject_cache_control and provider == "anthropic" and canon and isinstance(canon[-1], dict):
            canon[-1]["cache_control"] = {"type": "ephemeral"}
        return canon

    @classmethod
    def normalize_messages(
        cls,
        messages: List[Dict[str, Any]],
        provider: str = "generic",
        inject_cache_control: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Normalizes message list and optional tool schemas:
        1. Separates static system/developer prefix from dynamic turns
        2. Strips volatile timestamps, counters, and ephemeral request IDs
        3. For Anthropic/generic: injects multi-breakpoint 'cache_control: {"type": "ephemeral"}'
           (tools[-1], last static system message, and rolling penultimate turn messages[-2])
        4. For OpenAI: emits deterministic prompt_cache_key without mutating message shape
        """
        norm_tools = cls.normalize_tools(tools, provider=provider, inject_cache_control=False) if tools is not None else None
        if not messages:
            return messages, {
                "prefix_tokens": 0,
                "cache_hit_eligible": False,
                "cache_eligible": False,
                "cache_control_injected": False,
                "breakpoints_injected": 0,
                "provider": provider,
                "normalized_tools": norm_tools,
            }

        normalized: List[Dict[str, Any]] = []
        static_prefix_parts: List[str] = []
        last_static_idx = -1
        seen_non_static = False

        for idx, msg in enumerate(messages):
            msg_copy = dict(msg)
            role = msg_copy.get("role", "")
            content = msg_copy.get("content", "")

            if role in ("system", "developer") and not seen_non_static:
                if isinstance(content, str):
                    cleaned_content = cls.strip_volatile_elements(content)
                    msg_copy["content"] = cleaned_content
                    static_prefix_parts.append(cleaned_content)
                    last_static_idx = idx
                elif isinstance(content, list):
                    cleaned_blocks = []
                    for blk in content:
                        if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                            blk_copy = dict(blk)
                            blk_copy["text"] = cls.strip_volatile_elements(blk_copy["text"])
                            cleaned_blocks.append(blk_copy)
                            static_prefix_parts.append(blk_copy["text"])
                        else:
                            cleaned_blocks.append(blk)
                    msg_copy["content"] = cleaned_blocks
                    last_static_idx = idx
            else:
                seen_non_static = True
            normalized.append(msg_copy)

        tools_str = json.dumps(norm_tools, sort_keys=True, ensure_ascii=False) if norm_tools else ""
        prefix_str = "\n".join(p for p in (tools_str, *static_prefix_parts) if p)
        prefix_tokens = count_tokens(prefix_str) if prefix_str else 0
        prefix_hash = cls.compute_prefix_hash(prefix_str) if prefix_str else ""

        cache_eligible = prefix_tokens >= 64
        breakpoints_injected = 0
        supports_cc = provider in ("generic", "anthropic")
        existing_cc = _count_existing_cache_controls(norm_tools, normalized)

        if inject_cache_control and supports_cc:
            # Breakpoint #1: Tools schema tail (for Anthropic when tools are supplied)
            if provider == "anthropic" and norm_tools and len(norm_tools) > 0 and isinstance(norm_tools[-1], dict):
                if prefix_tokens >= 1024 and (existing_cc + breakpoints_injected) < 4 and "cache_control" not in norm_tools[-1]:
                    norm_tools[-1]["cache_control"] = {"type": "ephemeral"}
                    breakpoints_injected += 1

            # Breakpoint #2: Static system/developer prefix tail
            if last_static_idx >= 0 and prefix_tokens >= 1024 and (existing_cc + breakpoints_injected) < 4:
                if _inject_cache_control_on_msg(normalized[last_static_idx]):
                    breakpoints_injected += 1

            # Breakpoint #3: Rolling history checkpoint (penultimate turn before new user input)
            if provider == "anthropic" and len(normalized) >= 3 and (existing_cc + breakpoints_injected) < 4:
                penultimate_idx = len(normalized) - 2
                if penultimate_idx > last_static_idx:
                    upto_tokens = prefix_tokens + sum(
                        count_tokens(_message_text(m))
                        for m in normalized[last_static_idx + 1 : penultimate_idx + 1]
                    )
                    if upto_tokens >= 1024 and _inject_cache_control_on_msg(normalized[penultimate_idx]):
                        breakpoints_injected += 1

        meta: Dict[str, Any] = {
            "prefix_tokens": prefix_tokens,
            "prefix_hash": prefix_hash,
            "cache_eligible": cache_eligible,
            "cache_control_injected": breakpoints_injected > 0,
            "breakpoints_injected": breakpoints_injected,
            "provider": provider,
        }
        if prefix_hash:
            meta["prompt_cache_key"] = prefix_hash[:32]
        if norm_tools is not None:
            meta["normalized_tools"] = norm_tools

        return normalized, meta
