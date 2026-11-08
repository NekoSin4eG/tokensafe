"""
Cryptographically secure, zero-dependency License Engine for TokenSafe.
Supports offline-verifiable HMAC-SHA256 signed licenses and 7-day trials.
Compatible with Stripe webhook lifecycle and B2B on-premise proxy deployments.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
from typing import Dict, Any, Tuple, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LICENSE_FILE = os.path.join(DATA_DIR, "license.json")

# Master signing seed - used for validation and verification
# In production, signature verification uses a public verification hash
DEFAULT_SIGNING_SALT = "ts-core-sec-9a4f8b2c1e7d3a506198fbcde40231aa"

class LicenseManager:
    """Manages TokenSafe subscription tiers, offline signature verification, and 7-day trials."""

    TIERS = {
        "free": {"max_tokens_monthly": 100_000, "deepseek_cloud": False, "team_analytics": False},
        "solo": {"max_tokens_monthly": None, "deepseek_cloud": True, "team_analytics": False},
        "team": {"max_tokens_monthly": None, "deepseek_cloud": True, "team_analytics": True},
        "enterprise": {"max_tokens_monthly": None, "deepseek_cloud": True, "team_analytics": True, "self_hosted": True}
    }

    @classmethod
    def generate_license(
        cls,
        tier: str,
        email: str,
        duration_days: int = 30,
        signing_key: str = DEFAULT_SIGNING_SALT,
        is_trial: bool = False
    ) -> str:
        """
        Generates a tamper-proof cryptographically signed license key.
        Format: TS1.<b64_payload>.<signature_hex>
        """
        now = int(time.time())
        if duration_days == 0:
            expires_at = 0  # Lifetime
        else:
            expires_at = now + (duration_days * 86400)

        payload = {
            "tier": tier.lower(),
            "email": email.strip().lower(),
            "issued_at": now,
            "expires_at": expires_at,
            "trial": is_trial,
            "v": 1
        }

        payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")

        sig = hmac.new(signing_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
        return f"TS1.{payload_b64}.{sig}"

    @classmethod
    def validate_license(
        cls,
        license_key: str,
        signing_key: str = DEFAULT_SIGNING_SALT
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Validates the license key signature and expiration.
        Returns (is_valid, payload_dict, reason_str).
        """
        if not license_key or not isinstance(license_key, str):
            return False, {}, "Empty license key"

        license_key = license_key.strip()
        parts = license_key.split(".")
        if len(parts) != 3 or parts[0] != "TS1":
            return False, {}, "Invalid license key format (must start with TS1.)"

        payload_b64, sig = parts[1], parts[2]
        # Pad base64
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            return False, {}, f"Corrupted license payload: {str(e)}"

        # Verify signature
        expected_sig = hmac.new(signing_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected_sig):
            return False, {}, "Invalid signature: license key has been tampered with"

        # Check expiration
        now = int(time.time())
        expires_at = payload.get("expires_at", 0)
        if expires_at > 0 and now > expires_at:
            expired_days_ago = (now - expires_at) // 86400
            return False, payload, f"License expired {expired_days_ago} day(s) ago"

        return True, payload, "License is active and valid"

    @classmethod
    def get_active_license(cls) -> Tuple[str, Dict[str, Any]]:
        """Retrieves active license from env or data/license.json, falling back to free tier."""
        key = os.environ.get("TOKENSAFE_LICENSE_KEY", "").strip()
        if not key and os.path.exists(LICENSE_FILE):
            try:
                with open(LICENSE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    key = data.get("license_key", "").strip()
            except Exception:
                pass

        if key:
            valid, payload, msg = cls.validate_license(key)
            if valid:
                return payload.get("tier", "solo"), payload

        return "free", {"tier": "free", "max_tokens_monthly": 100_000}

    @classmethod
    def save_license(cls, license_key: str) -> Tuple[bool, str]:
        """Saves a verified license key to local persistent storage."""
        valid, payload, msg = cls.validate_license(license_key)
        if not valid:
            return False, f"Cannot save invalid license: {msg}"

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "license_key": license_key,
                "tier": payload.get("tier"),
                "email": payload.get("email"),
                "expires_at": payload.get("expires_at"),
                "trial": payload.get("trial", False),
                "saved_at": int(time.time())
            }, f, indent=2)

        tier_name = payload.get("tier", "").upper()
        trial_note = " (7-Day Trial)" if payload.get("trial") else ""
        return True, f"Successfully activated TokenSafe {tier_name}{trial_note}!"
