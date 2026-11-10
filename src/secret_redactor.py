"""
Secret Redactor: Intercepts and scrubs sensitive credentials and API keys
from prompts, context payloads, and agent tool responses before leaving the local machine.
Replaces detected secrets with mock anchors to prevent leaks and save tokens.
"""

import re
from typing import Tuple, Dict, Any, List, Optional

class SecretRedactor:
    """Detects and redacts high-risk secrets and credentials in text."""

    SAFE_PLACEHOLDERS = (
        "mock", "test", "dummy", "placeholder", "changeme", "example",
        "your_", "fake", "sample", "none", "null", "undefined",
        "localhost", "127.0.0.1", "0.0.0.0"
    )

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

    @classmethod
    def _is_safe_placeholder(cls, val: str) -> bool:
        """Determines if a fallback value is an innocuous placeholder/mock rather than a real secret."""
        if not val or len(val.strip()) == 0:
            return True
        v = val.strip().lower()
        if any(w in v for w in cls.SAFE_PLACEHOLDERS):
            return True
        # Check all identical characters (e.g. "00000000000000000000000000000000...")
        if len(set(v)) <= 2:
            return True
        # Short non-secret strings (less than 16 chars)
        if len(v) < 16 and not any(v.startswith(prefix) for prefix in ("sk_", "whsec_", "ghp_", "ts_sec_", "xoxb-")):
            return True
        return False

    @classmethod
    def _is_secret_var_name(cls, name: str) -> bool:
        """Checks if variable name represents a sensitive credential while ignoring public keys."""
        n = name.upper()
        if any(pub in n for pub in ("PUBLIC", "PUBKEY", "PUB_KEY", "_PUB", "PUB_HEX")):
            return False
        secret_markers = ("KEY", "SECRET", "TOKEN", "PASS", "AUTH", "CREDENTIAL", "PRIV")
        return any(m in n for m in secret_markers)

    @classmethod
    def check_secret_fallback(cls, code_snippet: str, file_path: str = "") -> Tuple[bool, Optional[str]]:
        """
        Inspects code edits and file writes to detect dangerous hardcoded secrets
        or private key fallbacks in environment variable loading.
        Returns: (is_dangerous, reason_message)
        """
        if not code_snippet or not isinstance(code_snippet, str):
            return False, None

        is_test = bool(file_path and any(p in file_path.lower() for p in ("test", "mock", "fixture")))

        # 1. Live provider keys (Stripe live, AWS, OpenAI, Anthropic, GitHub) anywhere
        if re.search(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{24,}\b", code_snippet):
            return True, "Detected live Stripe secret key hardcoded in code"
        if re.search(r"\bwhsec_[0-9a-zA-Z]{32,}\b", code_snippet):
            return True, "Detected Stripe webhook signing secret hardcoded in code"
        if re.search(r"\b(?:AKIA|ASIA|AROA)[A-Z0-9]{16}\b", code_snippet):
            return True, "Detected live AWS access key ID hardcoded in code"
        if re.search(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b", code_snippet):
            return True, "Detected live OpenAI project API key hardcoded in code"
        if re.search(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b", code_snippet):
            return True, "Detected live Anthropic API key hardcoded in code"
        if re.search(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{36}\b", code_snippet):
            return True, "Detected GitHub personal access token hardcoded in code"
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", code_snippet):
            if not is_test:
                return True, "Detected raw PEM private key block hardcoded in non-test source code"

        # 2. Python environment variable fallback patterns: os.environ.get("...KEY|SECRET...", "val")
        env_py_matches = re.finditer(
            r"""os\.(?:environ\.get|getenv)\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
            code_snippet,
            re.IGNORECASE
        )
        for m in env_py_matches:
            var_name, fallback_val = m.group(1), m.group(2)
            if cls._is_secret_var_name(var_name) and not cls._is_safe_placeholder(fallback_val):
                return True, f"Dangerous hardcoded fallback for sensitive environment variable '{var_name}'"

        # 3. JS/TS: process.env.XYZ_KEY || "val" or ?? "val"
        env_js_matches = re.finditer(
            r"""process\.env\.([A-Za-z0-9_]+)\s*(?:\|\||\?\?)\s*['"]([^'"]+)['"]""",
            code_snippet,
            re.IGNORECASE
        )
        for m in env_js_matches:
            var_name, fallback_val = m.group(1), m.group(2)
            if cls._is_secret_var_name(var_name) and not cls._is_safe_placeholder(fallback_val):
                return True, f"Dangerous hardcoded fallback for sensitive environment variable 'process.env.{var_name}'"

        # 4. Raw 64-character hexadecimal private key assignments in non-test code
        if not is_test:
            hex_priv_match = re.search(
                r"""(?:^|[\s;])(?:DEFAULT_)?(?:ED25519_|SECP256K1_|RSA_)?(?:PRIV|PRIVATE)_(?:KEY|HEX)\s*=\s*(?:os\.(?:environ\.get|getenv)\([^)]*\)\s*or\s*)?['"]([0-9a-fA-F]{64})['"]""",
                code_snippet,
                re.MULTILINE
            )
            if hex_priv_match:
                val = hex_priv_match.group(1)
                if not cls._is_safe_placeholder(val):
                    return True, "Detected raw 64-character hexadecimal private key assigned in source code"

        return False, None
