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
            re.compile(r"""(?i)(['"]?(?:aws_secret_access_key|aws_secret_key)['"]?\s*[:=]\s*)(['"]?)([A-Za-z0-9/+=]{40})\2"""),
            r'\1"[REDACTED_AWS_SECRET_KEY]"'
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
            re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|rediss):\/\/)([^:\s\"'/@]+):([^\s\"'@]+)(@)([\w\.\-]+(?::\d+)?(?:/[^\s\"')]*)?)"),
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
            re.compile(r"\bsk-(?:[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}|[a-zA-Z0-9]{48,})\b"),
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
            re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b"),
            "ghp_MOCK_GITHUB_TOKEN_ANCHOR"
        ),
        (
            "slack_token",
            re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{24,}\b"),
            "xoxb-MOCK_SLACK_TOKEN_ANCHOR"
        ),

        # Private Keys (RSA / DSA / EC / OPENSSH / ENCRYPTED) — idempotent against our anchor
        (
            "private_key",
            re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRI"
                r"VATE KEY-----(?!\s*\[REDACTED_PRIVATE_KEY_MOCK_ANCHOR\])[\s\S]+?"
                r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRI"
                r"VATE KEY-----"
            ),
            "-----BEGIN PRI" "VATE KEY-----\n[REDACTED_PRIVATE_KEY_MOCK_ANCHOR]\n-----END PRI" "VATE KEY-----"
        ),
    ]

    _TEST_FILE_RE = re.compile(
        r"(?:^|/)(?:__tests__|__mocks__|tests?|specs?|mocks?|fixtures?)(?:/|$)"
        r"|(?:^|/)(?:test_[^/]+|mock_[^/]+|[^/]+_test\.[^/]+|[^/]+_spec\.[^/]+|[^/]+\.test\.[^/]+|[^/]+\.spec\.[^/]+|conftest\.py)$",
        re.IGNORECASE,
    )
    _SAFE_PH_RE = re.compile(
        r"(?:^|[_\-\s./:])(?:mock|test|dummy|placeholder|changeme|example|your|fake|sample|none|null|undefined|redacted|localhost|127\.0\.0\.1|0\.0\.0\.0)(?:$|[_\-\s./:])",
        re.IGNORECASE,
    )
    _SECRET_VAR_RE = re.compile(
        r"(?:^|_)(?:API_KEY|ACCESS_KEY|SECRET_KEY|PRIVATE_KEY|SIGNING_KEY|ENCRYPTION_KEY|MASTER_KEY|JWT_KEY|CLIENT_SECRET|WEBHOOK_SECRET|SECRET|TOKEN|PASSWORD|PASSWD|PWD|CREDENTIAL|CREDENTIALS|AUTH_TOKEN|AUTH_SECRET|PRIV_KEY)(?:$|_)",
        re.IGNORECASE,
    )
    _NON_SECRET_VAR_RE = re.compile(
        r"(?:PUBLIC|PUBKEY|PUB_KEY|_PUB\b|PUB_HEX|PRIVACY|AUTHOR|BYPASS|CACHE_KEY|SORT_KEY|STORAGE_KEY|LOCAL_STORAGE_KEY)",
        re.IGNORECASE,
    )
    _ENV_PY_CALL_RE = re.compile(
        r"""os\.(?:environ\.get|getenv)\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*(?:default\s*=\s*)?['"]([^'"]+)['"]\s*\)|\)\s*or\s*['"]([^'"]+)['"])""",
        re.IGNORECASE,
    )
    _ENV_JS_RE = re.compile(
        r"""process\.env(?:\.([A-Za-z0-9_]+)|\[\s*['"]([A-Za-z0-9_]+)['"]\s*\])\s*(?:\|\||\?\?)\s*['"`]([^'"`\n]+)['"`]""",
        re.IGNORECASE,
    )
    _HEX_PRIV_RE = re.compile(
        r"""(?:^|[\s;])(?:DEFAULT_)?(?:ED25519_|SECP256K1_|RSA_)?(?:PRIV|PRIVATE)_(?:KEY|HEX)\s*=\s*(?:os\.(?:environ\.get|getenv)\([^)]*\)\s*or\s*)?['"]([0-9a-fA-F]{64})['"]""",
        re.MULTILINE,
    )
    _LIVE_CHECKS = (
        (re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{24,}\b"), "Detected live Stripe secret key hardcoded in code"),
        (re.compile(r"\bwhsec_[0-9a-zA-Z]{32,}\b"), "Detected Stripe webhook signing secret hardcoded in code"),
        (re.compile(r"\b(?:AKIA|ASIA|AROA)[A-Z0-9]{16}\b"), "Detected live AWS access key ID hardcoded in code"),
        (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"), "Detected live OpenAI project API key hardcoded in code"),
        (re.compile(r"\bsk-(?:[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}|[a-zA-Z0-9]{48,})\b"), "Detected live OpenAI legacy API key hardcoded in code"),
        (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"), "Detected live Anthropic API key hardcoded in code"),
        (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "Detected GitHub personal access token hardcoded in code"),
        (re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{24,}\b"), "Detected Slack API token hardcoded in code"),
    )

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
            redacted, n_subs = pattern.subn(repl, redacted)
            if n_subs > 0:
                total_redacted += n_subs
                detected_types.append(name)

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
        if v in cls.SAFE_PLACEHOLDERS or cls._SAFE_PH_RE.search(v):
            return True
        # Check all identical characters (e.g. "00000000000000000000000000000000...")
        if len(set(v)) <= 2:
            return True
        return False

    @classmethod
    def _is_secret_var_name(cls, name: str) -> bool:
        """Checks if variable name represents a sensitive credential while ignoring public/non-secret keys."""
        n = name.upper()
        if cls._NON_SECRET_VAR_RE.search(n):
            return False
        return bool(cls._SECRET_VAR_RE.search(n))

    @classmethod
    def check_secret_fallback(cls, code_snippet: str, file_path: str = "") -> Tuple[bool, Optional[str]]:
        """
        Inspects code edits and file writes to detect dangerous hardcoded secrets
        or private key fallbacks in environment variable loading.
        Returns: (is_dangerous, reason_message)
        """
        if not code_snippet or not isinstance(code_snippet, str):
            return False, None

        is_test = bool(file_path and cls._TEST_FILE_RE.search(file_path.replace("\\", "/")))

        # 1. Live provider keys (Stripe, AWS, OpenAI, Anthropic, GitHub, Slack) anywhere
        for pat, err_msg in cls._LIVE_CHECKS:
            if pat.search(code_snippet):
                return True, err_msg
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----", code_snippet):
            if not is_test:
                return True, "Detected raw PEM private key block hardcoded in non-test source code"

        # 2. Python environment variable fallback patterns: os.environ.get("...", "val") or os.getenv(...) or "val"
        for m in cls._ENV_PY_CALL_RE.finditer(code_snippet):
            var_name = m.group(1)
            fallback_val = m.group(2) if m.group(2) is not None else m.group(3)
            if fallback_val is not None and cls._is_secret_var_name(var_name) and not cls._is_safe_placeholder(fallback_val):
                return True, f"Dangerous hardcoded fallback for sensitive environment variable '{var_name}'"

        # 3. JS/TS: process.env.XYZ_KEY || "val" or process.env["XYZ_KEY"] ?? "val"
        for m in cls._ENV_JS_RE.finditer(code_snippet):
            var_name = m.group(1) or m.group(2) or ""
            fallback_val = m.group(3)
            if cls._is_secret_var_name(var_name) and not cls._is_safe_placeholder(fallback_val):
                return True, f"Dangerous hardcoded fallback for sensitive environment variable 'process.env.{var_name}'"

        # 4. Raw 64-character hexadecimal private key assignments in non-test code
        if not is_test:
            hex_priv_match = cls._HEX_PRIV_RE.search(code_snippet)
            if hex_priv_match:
                val = hex_priv_match.group(1)
                if not cls._is_safe_placeholder(val):
                    return True, "Detected raw 64-character hexadecimal private key assigned in source code"

        return False, None
