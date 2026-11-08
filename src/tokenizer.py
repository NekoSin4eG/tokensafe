"""
Zero-dependency, high-accuracy BPE/LLM Token Estimator.
Optimized for Gemini, Claude, and OpenAI tokenization patterns.
Supports English, Cyrillic, CJK, and code-heavy payloads.
"""

import re
from typing import Optional

try:
    import tiktoken  # type: ignore
    _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    _TIKTOKEN_ENCODING = None


class TokenCounter:
    """Estimates or calculates token counts across languages and code structures."""

    # Robust regex splitting on words, contractions, numbers, punctuation, whitespace
    _WORD_REGEX = re.compile(
        r"""'s|'t|'re|'ve|'m|'ll|'d|[a-zA-Zа-яА-ЯёЁ_]+|\d{1,3}|[^\w\s]|\s+"""
    )

    @classmethod
    def count(cls, text: Optional[str]) -> int:
        """Calculate or estimate token count for any text."""
        if not text:
            return 0

        # If tiktoken is installed, use exact OpenAI BPE cl100k
        if _TIKTOKEN_ENCODING is not None:
            try:
                return len(_TIKTOKEN_ENCODING.encode(text))
            except Exception:
                pass

        # High-precision heuristic BPE emulation
        return cls._estimate_tokens(text)

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        tokens = 0
        chunks = cls._WORD_REGEX.findall(text)

        for chunk in chunks:
            # Check if whitespace
            if chunk.isspace():
                if "\n" in chunk:
                    tokens += chunk.count("\n")
                    remaining_spaces = len(chunk) - chunk.count("\n")
                    tokens += remaining_spaces // 4
                else:
                    # In BPE, single spaces are merged into adjacent word tokens (e.g. " world").
                    # Only indentations (e.g., 4 spaces) add extra tokens.
                    tokens += len(chunk) // 4
                continue

            # Check if punctuation / code symbol
            if not chunk.isalnum() and "_" not in chunk:
                # Group of symbols: e.g. "();" -> 1 token per 1-2 symbols
                tokens += max(1, (len(chunk) + 1) // 2)
                continue

            # Check if numeric
            if chunk.isdigit():
                tokens += max(1, (len(chunk) + 2) // 3)
                continue

            # Alphabetic words: Check script
            is_cyrillic = any('\u0400' <= char <= '\u04FF' for char in chunk)
            is_cjk = any('\u4E00' <= char <= '\u9FFF' for char in chunk)

            if is_cjk:
                # CJK: typically ~1.8 tokens per character
                tokens += int(len(chunk) * 1.8) + 1
            elif is_cyrillic:
                # Cyrillic: BPE splits into ~2-3 char chunks
                if len(chunk) <= 3:
                    tokens += 1
                elif len(chunk) <= 6:
                    tokens += 2
                elif len(chunk) <= 9:
                    tokens += 3
                else:
                    tokens += max(3, (len(chunk) + 2) // 3)
            else:
                # English / Code identifier
                subwords = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+', chunk)
                if subwords:
                    for sub in subwords:
                        if len(sub) <= 7:
                            tokens += 1
                        elif len(sub) <= 12:
                            tokens += 2
                        else:
                            tokens += max(2, (len(sub) + 4) // 5)
                else:
                    if len(chunk) <= 7:
                        tokens += 1
                    else:
                        tokens += max(1, (len(chunk) + 4) // 5)

        return max(1, tokens) if text.strip() else 0


def count_tokens(text: Optional[str]) -> int:
    """Convenience helper."""
    return TokenCounter.count(text)
