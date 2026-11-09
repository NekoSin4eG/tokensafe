"""
Distillation Cache: Two-tier (In-Memory LRU + SQLite) cache for semantic prompt distillations.
Prevents duplicate LLM provider API roundtrips (DeepSeek / Gemini / OpenAI).
Zero external runtime dependencies.
"""

import os
import hashlib
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Optional, Dict, Any

class DistillationCache:
    """Thread-safe two-tier cache (In-Memory LRU + Persistent SQLite)."""

    _lock = threading.Lock()
    _memory_cache: OrderedDict = OrderedDict()
    _MAX_MEMORY_ENTRIES = 1000
    _db_path: Optional[str] = None
    _initialized_dbs: set = set()
    _hits: int = 0
    _misses: int = 0

    @classmethod
    def _normalize_prompt(cls, prompt_text: str) -> str:
        """Strips and normalizes whitespace."""
        return " ".join((prompt_text or "").strip().split())

    @classmethod
    def _hash_prompt(cls, prompt_text: str) -> str:
        """Returns SHA-256 hash of normalized prompt text."""
        norm = cls._normalize_prompt(prompt_text)
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    @classmethod
    def _get_db_path(cls) -> str:
        if cls._db_path:
            return cls._db_path

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.environ.get("TOKENSAFE_DATA_DIR", os.path.join(base_dir, "data"))
        os.makedirs(data_dir, exist_ok=True)
        cls._db_path = os.path.join(data_dir, "metrics.db")
        return cls._db_path

    @classmethod
    def _init_db(cls, conn: sqlite3.Connection):
        db_path = cls._get_db_path()
        if db_path in cls._initialized_dbs:
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS distillation_cache (
                prompt_hash TEXT PRIMARY KEY,
                prompt_text TEXT,
                distilled_text TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_distill_hash ON distillation_cache(prompt_hash);")
        conn.commit()
        cls._initialized_dbs.add(db_path)

    @classmethod
    def get(cls, prompt_text: str) -> Optional[str]:
        """Retrieves cached distillation S-Expression if present."""
        if not prompt_text or not prompt_text.strip():
            return None

        h = cls._hash_prompt(prompt_text)

        with cls._lock:
            # 1. Check in-memory LRU
            if h in cls._memory_cache:
                cls._hits += 1
                distilled = cls._memory_cache.pop(h)
                cls._memory_cache[h] = distilled # Move to end (most recently used)
                return distilled

        # 2. Check SQLite persistent cache
        db_path = cls._get_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cls._init_db(conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT distilled_text, hit_count FROM distillation_cache WHERE prompt_hash = ?",
                (h,)
            )
            row = cursor.fetchone()
            if row:
                distilled_text, hit_count = row[0], row[1]
                cursor.execute(
                    "UPDATE distillation_cache SET hit_count = ? WHERE prompt_hash = ?",
                    (hit_count + 1, h)
                )
                conn.commit()

                with cls._lock:
                    cls._hits += 1
                    cls._memory_cache[h] = distilled_text
                    if len(cls._memory_cache) > cls._MAX_MEMORY_ENTRIES:
                        cls._memory_cache.popitem(last=False) # Evict oldest

                return distilled_text
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        # 3. Check L3 Shared Team Cache (TokenSafe Team tier)
        try:
            from src.team_cache import TeamCache
            team_val = TeamCache.get("PROMPT", prompt_text)
            if team_val:
                cls.set(prompt_text, team_val, provider="team_cache", sync_remote=False)
                with cls._lock:
                    cls._hits += 1
                return team_val
        except Exception:
            pass

        with cls._lock:
            cls._misses += 1
        return None

    @classmethod
    def set(cls, prompt_text: str, distilled_text: str, provider: str = "deepseek", sync_remote: bool = True) -> None:
        """Stores a distillation result into memory LRU, SQLite cache, and Team Cache if applicable."""
        if not prompt_text or not distilled_text or not distilled_text.strip():
            return

        h = cls._hash_prompt(prompt_text)
        norm_prompt = cls._normalize_prompt(prompt_text)
        now_iso = datetime.now().isoformat()

        with cls._lock:
            cls._memory_cache[h] = distilled_text.strip()
            if len(cls._memory_cache) > cls._MAX_MEMORY_ENTRIES:
                cls._memory_cache.popitem(last=False)

        db_path = cls._get_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cls._init_db(conn)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO distillation_cache (prompt_hash, prompt_text, distilled_text, provider, created_at, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(prompt_hash) DO UPDATE SET
                    distilled_text = excluded.distilled_text,
                    provider = excluded.provider
            """, (h, norm_prompt[:500], distilled_text.strip(), provider, now_iso))
            conn.commit()
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        # 4. Broadcast to L3 Shared Team Cache (Asynchronous)
        if sync_remote:
            try:
                from src.team_cache import TeamCache
                TeamCache.put("PROMPT", prompt_text, distilled_text.strip(), tokens_saved=45)
            except Exception:
                pass

    @classmethod
    def clear(cls) -> None:
        """Clears both in-memory and persistent SQLite cache."""
        with cls._lock:
            cls._memory_cache.clear()
            cls._hits = 0
            cls._misses = 0

        db_path = cls._get_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cls._init_db(conn)
            conn.execute("DELETE FROM distillation_cache;")
            conn.commit()
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Returns runtime cache statistics."""
        with cls._lock:
            mem_size = len(cls._memory_cache)
            hits = cls._hits
            misses = cls._misses

        total_db_entries = 0
        db_path = cls._get_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cls._init_db(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM distillation_cache")
            row = cursor.fetchone()
            if row:
                total_db_entries = row[0]
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        total_lookups = hits + misses
        hit_ratio = (hits / total_lookups * 100.0) if total_lookups > 0 else 0.0

        return {
            "memory_entries": mem_size,
            "persistent_entries": total_db_entries,
            "hits": hits,
            "misses": misses,
            "hit_ratio_pct": round(hit_ratio, 1)
        }
