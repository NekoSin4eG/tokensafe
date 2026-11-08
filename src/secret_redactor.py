"""
Secret Redactor: Intercepts and scrubs sensitive credentials and API keys
from prompts, context payloads, and agent tool responses before leaving the local machine.
Replaces detected secrets with mock anchors to prevent leaks and save tokens.
"""

import re
from typing import Tuple, Dict, Any, List

class SecretRedactor:
    """Detects and redacts high-risk secrets and credentials in text."""

    PATTERNS: List[Tuple[str, re.Pattern, str]] = [
        # Stripe API Keys (Secret, Restricted, Webhook)
        (
            "stripe_secret",
            re.compile(r"\b(sk|rk)_(live|test)_[0-9a-zA-Z]{24,}\b"),
            r"\1_\2_mock_tokensafe_anchor"
        ),
        (
            "stripe_webhook",
            re.compile(r"\bwhsec_[0-9a-zA-Z]{32,}\b"),
            "whsec_mock_tokensafe_anchor"
        ),

        # AWS Access Keys & Identifiers
        (
            "aws_access_key",
            re.compile(r"\b(AKIA|ASIA|AROA)[A-Z0-9]{16}\b"),
            r"\1_MOCK_AWS_KEY_ID"
        ),
        (
            "aws_secret_key",
            re.compile(r"(?i)(aws_secret_access_key|aws_secret_key)\s*([:=])\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
            r'\1\2"[REDACTED_AWS_SECRET_KEY]"'
        ),

        # JWT Tokens (Header.Payload.Signature format)
        (
            "jwt_token",
            re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
            "eyJ_MOCK_JWT_PAYLOAD_ANCHOR"
        ),

        # Database Connection Strings with Passwords
        (
            "db_connection_url",
            re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|rediss):\/\/)([^:\s\"'/]+):([^\s\"'/]+)(@)([\w\.\-]+(?::\d+)?(?:/[^\s\"')]*)?)"),
            r"\1\2:[REDACTED_DB_PWD]\4\5"
        ),

        # AI Provider Keys
        (
            "openai_key",
            re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"),
            "sk-proj-MOCK_OPENAI_KEY_ANCHOR"
        ),
        (
            "openai_legacy_key",
            re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
            "sk-MOCK_OPENAI_KEY_ANCHOR"
        ),
        (
            "anthropic_key",
            re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
            "sk-ant-MOCK_ANTHROPIC_KEY_ANCHOR"
        ),

        # VCS & Platform Tokens
        (
            "github_token",
            re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
            r"\1_MOCK_GITHUB_TOKEN_ANCHOR"
        ),
        (
            "slack_token",
            re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{24,}\b"),
            "xoxb-MOCK_SLACK_TOKEN_ANCHOR"
        ),

        # Private Keys (RSA / DSA / EC / OPENSSH)
        (
            "private_key",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
            "-----BEGIN PRIVATE KEY-----\n[REDACTED_PRIVATE_KEY_MOCK_ANCHOR]\n-----END PRIVATE KEY-----"
        ),
    ]

    @classmethod
    def redact_text(cls, text: str) -> Tuple[str, int, List[str]]:
        """
        Redacts all recognized secrets in the given text.
        Returns (redacted_text, count_redacted, secret_types_detected).
        """
        if not text or not isinstance(text, str):
            return text, 0, []

        redacted = text
        total_redacted = 0
        detected_types: List[str] = []

        for name, pattern, repl in cls.PATTERNS:
            matches = list(pattern.finditer(redacted))
            if matches:
                total_redacted += len(matches)
                detected_types.append(name)
                redacted = pattern.sub(repl, redacted)

        return redacted, total_redacted, detected_types

    @classmethod
    def redact_messages(cls, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        """
        Recursively scans and redacts chat message payloads in-place.
        Returns (messages, total_redactions).
        """
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                cleaned, count, _ = cls.redact_text(content)
                if count > 0:
                    msg["content"] = cleaned
                    total += count
            elif isinstance(content, list):
                # Multimodal or complex content blocks
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        cleaned, count, _ = cls.redact_text(block["text"])
                        if count > 0:
                            block["text"] = cleaned
                            total += count

        return messages, total
