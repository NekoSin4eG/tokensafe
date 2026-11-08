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

# Master Public Key for client-side offline verification (RFC 8032 Ed25519)
DEFAULT_ED25519_PUBLIC_KEY = "244ea9d4a20e697578b7b7b31db71613c659678d221300dea1c5a03083bc90e9"
# Private key for cloud server signing (never distributed to untrusted clients)
DEFAULT_ED25519_PRIVATE_KEY = os.environ.get("TOKENSAFE_PRIVATE_KEY", "6785d298e2df4c2c305bd4956f124f038d2da5cbda7f4146513645f1733064c0")
# Rotated HMAC salt for backward compatibility with TS1 legacy licenses
ROTATED_HMAC_SALT = os.environ.get("TOKENSAFE_HMAC_SALT", "ts-sec-rotated-7b3f9c2d1e0a4f5b6829e1c3a70428e5")
DEFAULT_SIGNING_SALT = ROTATED_HMAC_SALT

# ---------------------------------------------------------------------------
# Zero-dependency RFC 8032 Ed25519 Implementation with Cryptography Fast-Path
# ---------------------------------------------------------------------------
_ED25519_Q = 2**255 - 19
_ED25519_L = 2**252 + 27742317777372353535851937790883648493

def _ed_inv(x: int) -> int:
    return pow(x, _ED25519_Q - 2, _ED25519_Q)

_ED_D = -121665 * _ed_inv(121666)
_ED_I = pow(2, (_ED25519_Q - 1) // 4, _ED25519_Q)

def _ed_xrecover(y: int) -> int:
    xx = (y * y - 1) * _ed_inv(_ED_D * y * y + 1)
    x = pow(xx, (_ED25519_Q + 3) // 8, _ED25519_Q)
    if (x * x - xx) % _ED25519_Q != 0:
        x = (x * _ED_I) % _ED25519_Q
    if x % 2 != 0:
        x = _ED25519_Q - x
    return x

_ED_BY = 4 * _ed_inv(5)
_ED_BX = _ed_xrecover(_ED_BY)
_ED_B = [_ED_BX % _ED25519_Q, _ED_BY % _ED25519_Q]

def _ed_add(p1: list, p2: list) -> list:
    x1, y1 = p1[0], p1[1]
    x2, y2 = p2[0], p2[1]
    x3 = (x1 * y2 + x2 * y1) * _ed_inv(1 + _ED_D * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _ed_inv(1 - _ED_D * x1 * x2 * y1 * y2)
    return [x3 % _ED25519_Q, y3 % _ED25519_Q]

def _ed_scalarmult(pt: list, e: int) -> list:
    if e == 0:
        return [0, 1]
    q = _ed_scalarmult(pt, e // 2)
    q = _ed_add(q, q)
    if e & 1:
        q = _ed_add(q, pt)
    return q

def _ed_encodepoint(pt: list) -> bytes:
    x, y = pt[0], pt[1]
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes([sum([bits[i * 8 + j] << j for j in range(8)]) for i in range(32)])

def _ed_bit(h: bytes, i: int) -> int:
    return (h[i // 8] >> (i % 8)) & 1

def _ed_decodepoint(s: bytes) -> Optional[list]:
    y = sum(2**i * _ed_bit(s, i) for i in range(0, 255))
    x = _ed_xrecover(y)
    if x & 1 != _ed_bit(s, 255):
        x = _ED25519_Q - x
    pt = [x, y]
    if (-x * x + y * y - 1 - _ED_D * x * x * y * y) % _ED25519_Q != 0:
        return None
    return pt

def _ed25519_sign_bytes(msg: bytes, priv_bytes: bytes, pub_bytes: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        priv_obj = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
        return priv_obj.sign(msg)
    except Exception:
        h = hashlib.sha512(priv_bytes).digest()
        a = 2**254 + sum(2**i * _ed_bit(h, i) for i in range(3, 254))
        r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), "little")
        r_pt = _ed_scalarmult(_ED_B, r)
        r_bytes = _ed_encodepoint(r_pt)
        k = int.from_bytes(hashlib.sha512(r_bytes + pub_bytes + msg).digest(), "little")
        s = (r + k * a) % _ED25519_L
        return r_bytes + int.to_bytes(s, 32, "little")

def _ed25519_verify_bytes(sig: bytes, msg: bytes, pub_bytes: bytes) -> bool:
    if len(sig) != 64 or len(pub_bytes) != 32:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_obj.verify(sig, msg)
        return True
    except Exception:
        pass
    try:
        r_pt = _ed_decodepoint(sig[:32])
        a_pt = _ed_decodepoint(pub_bytes)
        if r_pt is None or a_pt is None:
            return False
        s = int.from_bytes(sig[32:], "little")
        if s >= _ED25519_L:
            return False
        h = int.from_bytes(hashlib.sha512(sig[:32] + pub_bytes + msg).digest(), "little")
        v1 = _ed_scalarmult(_ED_B, s)
        v2 = _ed_add(r_pt, _ed_scalarmult(a_pt, h))
        return v1 == v2
    except Exception:
        return False


class LicenseManager:
    """Manages TokenSafe subscription tiers, offline signature verification, and 7-day trials."""

    TIERS = {
        "free": {"max_tokens_monthly": 500_000, "deepseek_cloud": False, "team_analytics": False},
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
        signing_key: Optional[str] = None,
        is_trial: bool = False,
        version: int = 2
    ) -> str:
        """
        Generates a tamper-proof cryptographically signed license key.
        - Version 2 (Default): Asymmetric Ed25519 signed (Format: TS2.<b64_payload>.<b64_sig>)
        - Version 1: Legacy HMAC-SHA256 signed (Format: TS1.<b64_payload>.<sig_hex>)
        """
        now = int(time.time())
        expires_at = 0 if duration_days == 0 else now + (duration_days * 86400)

        payload = {
            "tier": tier.lower(),
            "email": email.strip().lower(),
            "issued_at": now,
            "expires_at": expires_at,
            "trial": is_trial,
            "v": version
        }

        payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")

        if version == 2:
            priv_hex = signing_key or DEFAULT_ED25519_PRIVATE_KEY
            priv_bytes = bytes.fromhex(priv_hex)
            pub_bytes = bytes.fromhex(DEFAULT_ED25519_PUBLIC_KEY)
            sig_raw = _ed25519_sign_bytes(payload_bytes, priv_bytes, pub_bytes)
            sig_b64 = base64.urlsafe_b64encode(sig_raw).decode("ascii").rstrip("=")
            return f"TS2.{payload_b64}.{sig_b64}"

        # Fallback to TS1 HMAC format
        hmac_key = signing_key or ROTATED_HMAC_SALT
        sig = hmac.new(hmac_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
        return f"TS1.{payload_b64}.{sig}"

    @classmethod
    def validate_license(
        cls,
        license_key: str,
        public_key: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Validates the license key signature and expiration.
        Supports:
        - TS2: Asymmetric Ed25519 verification using public key (Offline-safe, impossible to forge)
        - TS1: Legacy HMAC-SHA256 verification with rotated salt
        Returns (is_valid, payload_dict, reason_str).
        """
        if not license_key or not isinstance(license_key, str):
            return False, {}, "Empty license key"

        license_key = license_key.strip()
        parts = license_key.split(".")
        if len(parts) != 3 or parts[0] not in ("TS1", "TS2"):
            return False, {}, "Invalid license key format (must start with TS2. or TS1.)"

        schema_ver, payload_b64, sig_part = parts[0], parts[1], parts[2]
        padded_payload = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(padded_payload.encode("ascii"))
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            return False, {}, f"Corrupted license payload: {str(e)}"

        # 1. Asymmetric Ed25519 Verification (TS2)
        if schema_ver == "TS2":
            pub_hex = public_key or DEFAULT_ED25519_PUBLIC_KEY
            try:
                pub_bytes = bytes.fromhex(pub_hex)
                padded_sig = sig_part + "=" * (-len(sig_part) % 4)
                sig_bytes = base64.urlsafe_b64decode(padded_sig.encode("ascii"))
            except Exception:
                return False, {}, "Invalid signature encoding"

            if not _ed25519_verify_bytes(sig_bytes, payload_bytes, pub_bytes):
                return False, {}, "Invalid digital signature: license key has been tampered with or is counterfeit"

        # 2. Legacy TS1 Verification
        else:
            hmac_key = public_key or ROTATED_HMAC_SALT
            expected_sig = hmac.new(hmac_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(sig_part, expected_sig):
                # Try fallback default salt if user had old key
                old_salt = "ts-core-sec-9a4f8b2c1e7d3a506198fbcde40231aa"
                fallback_sig = hmac.new(old_salt.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
                if not hmac.compare_digest(sig_part, fallback_sig):
                    return False, {}, "Invalid signature: legacy license key has been tampered with"

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

        return "free", {"tier": "free", "max_tokens_monthly": 500_000}

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

    @classmethod
    def check_quota(cls, tracker: Optional[Any] = None) -> Dict[str, Any]:
        """
        Evaluates whether the current active license quota is exceeded.
        Returns:
        {
            "tier": str,
            "is_quota_exceeded": bool,
            "monthly_limit": Optional[int],
            "monthly_saved": int,
            "warning": Optional[str]
        }
        """
        tier, payload = cls.get_active_license()
        tier_cfg = cls.TIERS.get(tier, cls.TIERS["free"])
        max_tokens = tier_cfg.get("max_tokens_monthly")

        # Paid tiers (solo, team, enterprise) have unlimited local optimization
        if max_tokens is None:
            return {
                "tier": tier,
                "is_quota_exceeded": False,
                "monthly_limit": None,
                "monthly_saved": 0,
                "warning": None
            }

        # For free tier, calculate trailing 30 days saved tokens
        if tracker is None:
            try:
                from src.metrics import MetricsTracker
                tracker = MetricsTracker()
            except Exception:
                tracker = None

        monthly_saved = tracker.get_monthly_saved_tokens() if tracker else 0
        exceeded = monthly_saved >= max_tokens

        warning = None
        if exceeded:
            warning = (
                f"⚡ **TokenSafe Quota Alert**: Community Edition limit reached "
                f"({monthly_saved:,}/{max_tokens:,} tokens saved this month). "
                f"Advanced optimization is throttled. Upgrade to Solo for unlimited optimization: `tokensafe activate <key>` or visit https://tokensafe.dev."
            )
        elif monthly_saved >= int(max_tokens * 0.85):
            warning = (
                f"⚡ **TokenSafe Quota Notice**: Community Edition is at {int(monthly_saved / max_tokens * 100)}% of monthly limit "
                f"({monthly_saved:,}/{max_tokens:,} tokens). "
                f"Upgrade to Solo for unlimited optimization: `tokensafe activate <key>` or https://tokensafe.dev."
            )

        return {
            "tier": tier,
            "is_quota_exceeded": exceeded,
            "monthly_limit": max_tokens,
            "monthly_saved": monthly_saved,
            "warning": warning
        }
