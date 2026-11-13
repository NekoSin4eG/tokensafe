"""
Cryptographically secure License Engine for TokenSafe.
Supports offline-verifiable Ed25519 (TS2) licenses, 7-day trials, and quota checks.
Compatible with Stripe webhook lifecycle and B2B on-premise proxy deployments.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import platform
import uuid
import threading
from typing import Dict, Any, Tuple, Optional, List

from src.paths import resolve_data_dir

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = resolve_data_dir()
LICENSE_FILE = os.path.join(DATA_DIR, "license.json")
GLOBAL_DATA_DIR = os.path.expanduser("~/.tokensafe")
GLOBAL_LICENSE_FILE = os.path.join(GLOBAL_DATA_DIR, "license.json")
_DEFAULT_LICENSE_FILE = LICENSE_FILE
_DEFAULT_GLOBAL_LICENSE_FILE = GLOBAL_LICENSE_FILE

# Master Public Key for client-side offline verification (RFC 8032 Ed25519).
# Hardcoded intentionally so environment overrides cannot substitute an attacker key.
EMBEDDED_ED25519_PUBLIC_KEY = "3911e2ffe5ad07ee23be392b88f238af302f9d3a0bf51881de8b5e0e6af71430"
DEFAULT_ED25519_PUBLIC_KEY = EMBEDDED_ED25519_PUBLIC_KEY
# Retired keys (never include unit-test public keys here)
LEGACY_ED25519_PUBLIC_KEYS: Tuple[str, ...] = ()
# Private key for cloud server signing (never distributed to client, loaded strictly from environment)
DEFAULT_ED25519_PRIVATE_KEY = os.environ.get("TOKENSAFE_PRIVATE_KEY", "")
# Server-only HMAC salt for legacy TS1 migration (never hardcoded in clients)
ROTATED_HMAC_SALT = os.environ.get("TOKENSAFE_HMAC_SALT", "")
DEFAULT_SIGNING_SALT = ROTATED_HMAC_SALT

# ---------------------------------------------------------------------------
# Required Cryptography-Backed RFC 8032 Ed25519 Verification & Signing
# ---------------------------------------------------------------------------
def _load_ed25519_module():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        return ed25519
    except ImportError as exc:
        raise RuntimeError(
            "TokenSafe requires 'cryptography>=42.0'. Install it via: pip install 'cryptography>=42.0'"
        ) from exc


def _ed25519_sign_bytes(msg: bytes, priv_bytes: bytes, pub_bytes: bytes = b"") -> bytes:
    ed25519 = _load_ed25519_module()
    priv_obj = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
    return priv_obj.sign(msg)


def _ed25519_verify_bytes(sig: bytes, msg: bytes, pub_bytes: bytes) -> bool:
    if len(sig) != 64 or len(pub_bytes) != 32:
        return False
    ed25519 = _load_ed25519_module()
    try:
        pub_obj = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_obj.verify(sig, msg)
        return True
    except Exception:
        return False


class LicenseManager:
    """Manages TokenSafe subscription tiers, offline signature verification, and 7-day trials."""

    # Successful signature verifications are cached per license key (thread-safe).
    # Rationale: the pure-Python Ed25519 fallback path costs ~0.6s per verification and
    # validate_license() runs on every lifecycle hook invocation (check_quota). The cache
    # TTL never outlives the license's own expiry window.
    _VALIDATION_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
    _VALIDATION_CACHE_LOCK = threading.Lock()
    _VALIDATION_CACHE_TTL = 3600.0  # 1 hour
    _VALIDATION_CACHE_MAX = 64
    _QUOTA_TURN_COUNTERS: Dict[str, int] = {}

    TIERS = {
        "free": {"max_tokens_monthly": 500_000, "team_analytics": False},
        "solo": {"max_tokens_monthly": None, "team_analytics": False},
        "team": {"max_tokens_monthly": None, "team_analytics": True},
        "enterprise": {"max_tokens_monthly": None, "team_analytics": True, "self_hosted": True}
    }

    @classmethod
    def get_machine_fingerprint(cls) -> str:
        """One-way anonymized machine identifier for server-side free-tier quota continuity."""
        raw = f"ts:{platform.system()}:{platform.node()}:{uuid.getnode()}:{os.path.expanduser('~')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def ensure_community_license(cls) -> Dict[str, Any]:
        """
        Fetches a signed Community Edition license (tier 'free') bound to this
        machine's anonymized fingerprint. Zero-config: no keys, no user action.

        Contract:
        - Skipped when any real license is already active (any tier).
        - Honors TOKENSAFE_TELEMETRY_DISABLED (same global consent switch as
          telemetry sync — no new flag).
        - Fully fail-open: any network/validation error leaves the embedded
          free-tier defaults untouched; nothing ever raises.
        - 24h retry cooldown so a server outage doesn't ping on every session.
        """
        state_path = ""
        try:
            state_path = os.path.join(resolve_data_dir(), "community_license_state.json")
            if os.environ.get("TOKENSAFE_TELEMETRY_DISABLED") in ("1", "true", "yes"):
                return {"success": False, "reason": "telemetry_disabled"}
            if cls.get_active_license_key():
                return {"success": False, "reason": "license_present"}

            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    last_attempt = int(json.load(f).get("last_attempt", 0))
                if time.time() - last_attempt < 86400:
                    return {"success": False, "reason": "cooldown"}
            except Exception:
                pass

            plugin_version = ""
            try:
                manifest_path = os.path.join(PROJECT_ROOT, "plugin.json")
                if os.path.exists(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        plugin_version = str(json.load(mf).get("version", "") or "")
            except Exception:
                plugin_version = ""

            import urllib.request

            cloud_url = os.environ.get("TOKENSAFE_CLOUD_URL", "https://api.tokensafe.dev").rstrip("/")
            req = urllib.request.Request(
                f"{cloud_url}/api/community/license",
                data=json.dumps({
                    "machine_fp": cls.get_machine_fingerprint(),
                    "platform": platform.system()[:32],
                    "arch": platform.machine()[:32],
                    "agent": os.environ.get("TOKENSAFE_AGENT", "")[:32],
                    "version": plugin_version[:32],
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "TokenSafe/Community-License"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            license_key = str(data.get("license_key", "") or "")
            if resp.status != 200 or not data.get("success") or not license_key:
                raise ValueError(str(data.get("error") or "empty community license response"))

            ok, msg = cls.save_license(license_key)
            if not ok:
                raise ValueError(msg)

            try:
                os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump({"last_attempt": int(time.time()), "issued": True}, f)
            except Exception:
                pass
            return {"success": True, "reason": "issued"}
        except Exception as exc:
            try:
                if state_path:
                    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump({"last_attempt": int(time.time()), "error": str(exc)[:200]}, f)
            except Exception:
                pass
            return {"success": False, "reason": "error"}

    @classmethod
    def generate_license(
        cls,
        tier: str,
        email: str,
        duration_days: int = 30,
        signing_key: Optional[str] = None,
        is_trial: bool = False,
        version: int = 2,
        org_id: Optional[str] = None,
        jti: Optional[str] = None,
        quota_monthly: Optional[int] = None
    ) -> str:
        """
        Generates a tamper-proof cryptographically signed license key.
        - Version 2 (Default): Asymmetric Ed25519 signed (Format: TS2.<b64_payload>.<b64_sig>)
        - Version 1: Legacy HMAC-SHA256 signed (Format: TS1.<b64_payload>.<sig_hex>)
        quota_monthly: optional server-managed monthly token quota embedded into
        the signed payload (used for Community Edition remote quota control).
        """
        now = int(time.time())
        expires_at = 0 if duration_days == 0 else now + (duration_days * 86400)

        payload: Dict[str, Any] = {
            "tier": tier.lower(),
            "email": email.strip().lower(),
            "issued_at": now,
            "expires_at": expires_at,
            "trial": is_trial,
            "v": version
        }
        if org_id:
            payload["org"] = org_id.strip()
        if jti:
            payload["jti"] = jti.strip()
        if quota_monthly:
            payload["max_tokens_monthly"] = int(quota_monthly)

        payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")

        if version == 2:
            priv_hex = signing_key or os.environ.get("TOKENSAFE_PRIVATE_KEY") or DEFAULT_ED25519_PRIVATE_KEY
            if not priv_hex:
                raise ValueError("TOKENSAFE_PRIVATE_KEY environment variable is required to generate license signatures.")
            priv_bytes = bytes.fromhex(priv_hex)
            pub_hex = os.environ.get("TOKENSAFE_PUBLIC_KEY") or DEFAULT_ED25519_PUBLIC_KEY
            pub_bytes = bytes.fromhex(pub_hex)
            sig_raw = _ed25519_sign_bytes(payload_bytes, priv_bytes, pub_bytes)
            sig_b64 = base64.urlsafe_b64encode(sig_raw).decode("ascii").rstrip("=")
            return f"TS2.{payload_b64}.{sig_b64}"

        # Fallback to TS1 HMAC format (server/test explicit signing_key only)
        hmac_key = signing_key or ROTATED_HMAC_SALT or os.environ.get("TOKENSAFE_HMAC_SALT", "")
        if not hmac_key:
            raise ValueError("TOKENSAFE_HMAC_SALT is required to sign legacy TS1 licenses.")
        sig = hmac.new(hmac_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
        return f"TS1.{payload_b64}.{sig}"

    @classmethod
    def validate_license(
        cls,
        license_key: str,
        public_key: Optional[str] = None,
        allow_legacy_ts1: bool = False,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Validates the license key signature and expiration.
        Supports:
        - TS2: Asymmetric Ed25519 verification using embedded public key (Offline-safe, impossible to forge)
        - TS1: Legacy HMAC-SHA256 rejected offline (server-side migration only when allow_legacy_ts1=True)
        Returns (is_valid, payload_dict, reason_str).
        """
        if not license_key or not isinstance(license_key, str):
            return False, {}, "Empty license key"

        license_key = license_key.strip()

        # Fast path: recently verified signature (avoids ~0.6s pure-Python Ed25519
        # verification on every hook invocation when the 'cryptography' package is absent)
        now = time.time()
        effective_pub = public_key or DEFAULT_ED25519_PUBLIC_KEY
        cache_key = f"{effective_pub}|{int(bool(allow_legacy_ts1))}|{license_key}"
        with cls._VALIDATION_CACHE_LOCK:
            cached = cls._VALIDATION_CACHE.get(cache_key)
            if cached and now < cached[1]:
                return True, dict(cached[0]), "License is active and valid (cached)"

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

            try:
                verified = _ed25519_verify_bytes(sig_bytes, payload_bytes, pub_bytes)
                if not verified and not public_key:
                    for leg_hex in LEGACY_ED25519_PUBLIC_KEYS:
                        try:
                            if _ed25519_verify_bytes(sig_bytes, payload_bytes, bytes.fromhex(leg_hex)):
                                verified = True
                                break
                        except RuntimeError:
                            raise
                        except Exception:
                            pass
            except RuntimeError as crypto_err:
                return False, {}, str(crypto_err)
            if not verified:
                return False, {}, "Invalid digital signature: license key has been tampered with or is counterfeit"

        # 2. Legacy TS1 Verification (disabled offline to eliminate public-salt forgery)
        else:
            if not allow_legacy_ts1:
                return (
                    False,
                    {},
                    "Legacy TS1 offline verification is disabled; reconnect to upgrade to a TS2 Ed25519 license.",
                )
            hmac_key = public_key or ROTATED_HMAC_SALT or os.environ.get("TOKENSAFE_HMAC_SALT", "")
            if not hmac_key:
                return False, {}, "Legacy TS1 verification requires server-side HMAC salt configuration"
            expected_sig = hmac.new(hmac_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(sig_part, expected_sig):
                return False, {}, "Invalid signature: legacy license key has been tampered with"

        # Check expiration
        now = int(time.time())
        expires_at = payload.get("expires_at", 0)
        if expires_at > 0 and now > expires_at:
            expired_days_ago = (now - expires_at) // 86400
            return False, payload, f"License expired {expired_days_ago} day(s) ago"

        # Cache the successful verification, bounded by both the TTL and the
        # license's own expiry so an expiring key is re-checked in time.
        ttl = cls._VALIDATION_CACHE_TTL
        if expires_at > 0:
            ttl = min(ttl, max(1.0, float(expires_at - now)))
        with cls._VALIDATION_CACHE_LOCK:
            if len(cls._VALIDATION_CACHE) >= cls._VALIDATION_CACHE_MAX:
                oldest = min(cls._VALIDATION_CACHE, key=lambda k: cls._VALIDATION_CACHE[k][1])
                cls._VALIDATION_CACHE.pop(oldest, None)
            cls._VALIDATION_CACHE[cache_key] = (payload, now + ttl)

        return True, payload, "License is active and valid"

    @classmethod
    def _candidate_license_files(cls) -> List[str]:
        """Returns license.json paths to inspect, honoring explicit TOKENSAFE_DATA_DIR and test patches."""
        explicit_dir = os.environ.get("TOKENSAFE_DATA_DIR", "").strip()
        if LICENSE_FILE != _DEFAULT_LICENSE_FILE:
            paths = [LICENSE_FILE]
            if GLOBAL_LICENSE_FILE != _DEFAULT_GLOBAL_LICENSE_FILE:
                paths.append(GLOBAL_LICENSE_FILE)
        elif explicit_dir:
            paths = [os.path.join(explicit_dir, "license.json")]
        else:
            paths = [LICENSE_FILE, GLOBAL_LICENSE_FILE]

        seen = set()
        unique: List[str] = []
        for p in paths:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                unique.append(p)
        return unique

    @classmethod
    def _resolve_active_license_record(cls) -> Tuple[Optional[Tuple[str, Dict[str, Any]]], str]:
        """Selects the freshest valid license across env and candidate files."""
        env_key = os.environ.get("TOKENSAFE_LICENSE_KEY", "").strip()
        if env_key:
            valid, payload, _ = cls.validate_license(env_key)
            return ((env_key, payload) if valid else None), env_key

        best_valid: Optional[Tuple[Tuple[int, int, float], str, Dict[str, Any]]] = None
        best_raw: Optional[Tuple[Tuple[int, float], str]] = None

        for path in cls._candidate_license_files():
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candidate = str(data.get("license_key", "")).strip()
                if not candidate:
                    continue
                saved_at = int(data.get("saved_at") or 0)
                mtime = os.path.getmtime(path)
            except Exception:
                continue

            raw_rank = (saved_at, mtime)
            if best_raw is None or raw_rank > best_raw[0]:
                best_raw = (raw_rank, candidate)

            valid, payload, _ = cls.validate_license(candidate)
            if valid:
                issued_at = int(payload.get("issued_at") or 0)
                valid_rank = (saved_at, issued_at, mtime)
                if best_valid is None or valid_rank > best_valid[0]:
                    best_valid = (valid_rank, candidate, payload)

        if best_valid is not None:
            return (best_valid[1], best_valid[2]), best_valid[1]
        return None, (best_raw[1] if best_raw is not None else "")

    @classmethod
    def get_active_license(cls) -> Tuple[str, Dict[str, Any]]:
        """Retrieves active license from env or persistent license.json files, falling back to free tier."""
        resolved, _ = cls._resolve_active_license_record()
        if resolved is not None:
            _, payload = resolved
            return payload.get("tier", "solo"), payload

        return "free", {"tier": "free", "max_tokens_monthly": 500_000}

    @classmethod
    def get_active_license_key(cls) -> str:
        """Retrieves the freshest active raw license key string from env or persistent license.json files."""
        _, key = cls._resolve_active_license_record()
        return key

    @classmethod
    def save_license(cls, license_key: str) -> Tuple[bool, str]:
        """Saves a verified license key to local and user global persistent storage."""
        valid, payload, msg = cls.validate_license(license_key)
        if not valid:
            return False, f"Cannot save invalid license: {msg}"

        record = {
            "license_key": license_key,
            "tier": payload.get("tier"),
            "email": payload.get("email"),
            "expires_at": payload.get("expires_at"),
            "trial": payload.get("trial", False),
            "saved_at": int(time.time())
        }

        for target_file in cls._candidate_license_files():
            target_dir = os.path.dirname(target_file)
            try:
                if target_dir:
                    os.makedirs(target_dir, exist_ok=True)
                tmp_path = f"{target_file}.tmp.{os.getpid()}"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(record, f, indent=2)
                os.replace(tmp_path, target_file)
            except Exception:
                pass

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

        # Server-issued quota (community license payload) overrides the embedded
        # default — enables remote quota control without a client release.
        if max_tokens is not None and isinstance(payload, dict):
            try:
                server_quota = int(payload.get("max_tokens_monthly") or 0)
                if server_quota > 0:
                    max_tokens = server_quota
            except (TypeError, ValueError):
                pass

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
                f"({monthly_saved:,}/{max_tokens:,} measured tokens saved this month). "
                f"Graph/signature AST slicing, pointer dedup, history compaction & KV-cache prefix locking are paused "
                f"(Security Guard stays active). Upgrade to Solo ($9/mo) or 7-Day Team Trial: "
                f"`tokensafe activate <key>` or visit https://tokensafe.dev/#pricing"
            )
        elif monthly_saved >= int(max_tokens * 0.85):
            warning = (
                f"⚡ **TokenSafe Quota Notice**: Community Edition is at {int(monthly_saved / max_tokens * 100)}% of monthly limit "
                f"({monthly_saved:,}/{max_tokens:,} measured tokens). "
                f"Upgrade to Solo for unlimited optimization: `tokensafe activate <key>` or https://tokensafe.dev/#pricing"
            )

        return {
            "tier": tier,
            "is_quota_exceeded": exceeded,
            "monthly_limit": max_tokens,
            "monthly_saved": monthly_saved,
            "remaining_tokens": max(0, max_tokens - monthly_saved),
            "warning": warning
        }

    @classmethod
    def should_emit_quota_banner(
        cls,
        tracker: Optional[Any] = None,
        session_id: str = "default_session",
        every_n_turns: int = 5,
    ) -> bool:
        """Returns True on turn #1 of quota exceedance per session and every N turns thereafter."""
        sid = session_id or "default_session"
        n = max(1, int(every_n_turns or 5))
        if tracker is not None and hasattr(tracker, "_get_connection"):
            try:
                with tracker._get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS quota_banner_state (
                            session_id TEXT PRIMARY KEY,
                            turns INTEGER NOT NULL DEFAULT 0,
                            updated_at INTEGER NOT NULL
                        )
                    """)
                    conn.execute("""
                        INSERT INTO quota_banner_state (session_id, turns, updated_at)
                        VALUES (?, 1, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            turns = quota_banner_state.turns + 1,
                            updated_at = excluded.updated_at
                    """, (sid, int(time.time())))
                    row = conn.execute(
                        "SELECT turns FROM quota_banner_state WHERE session_id = ?",
                        (sid,),
                    ).fetchone()
                    turn_no = int(row["turns"]) if row else 1
                return turn_no == 1 or (turn_no % n == 0)
            except Exception:
                pass
        with cls._VALIDATION_CACHE_LOCK:
            turn_no = cls._QUOTA_TURN_COUNTERS.get(sid, 0) + 1
            cls._QUOTA_TURN_COUNTERS[sid] = turn_no
        return turn_no == 1 or (turn_no % n == 0)
