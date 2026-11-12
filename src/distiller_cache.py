"""
Distillation Cache: Two-tier (In-Memory LRU + SQLite) cache for semantic prompt distillations.
Prevents duplicate LLM provider API roundtrips (DeepSeek / Gemini / OpenAI).
Zero external runtime dependencies.
"""

import os
import re
import hashlib
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from src.paths import resolve_data_dir


_COMBINED_SLOT_RE = re.compile(
    r"(?P<file>(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_-]+\."
    r"(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|cs|cpp|c|h|hpp|rb|php|kt|swift|css|scss|json|yaml|yml|toml|md|sql|sh)\b)"
    r"|`(?P<bt_sym>[A-Za-z_][A-Za-z0-9_]*)`"
    r"|\b(?P<sym>[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+|[a-z]+[A-Z][a-zA-Z0-9]*|[A-Z][a-z]+[A-Z][a-zA-Z0-9]*)\b"
)
_UNRESOLVED_SLOT_RE = re.compile(r"<(?:FILE|SYM)_\d+>")


_BRAND_PROPER_NOUNS = {
    "github", "gitlab", "bitbucket", "deepseek", "openai", "anthropic",
    "typescript", "javascript", "postgresql", "postgres", "mongodb",
    "macos", "iphone", "ipad", "next.js", "node.js", "nuxt.js", "vue.js",
    "cloudflare", "fastapi", "pytorch", "tensorflow", "graphql", "tailwind",
    "oauth", "oauth2", "jwt", "tokensafe", "antigravity",
}


class DistillationCache:
    """Thread-safe two-tier cache (In-Memory LRU + Persistent SQLite) with parametric slot templates."""

    _lock = threading.Lock()
    _memory_cache: OrderedDict = OrderedDict()
    _template_memory_cache: OrderedDict = OrderedDict()
    _MAX_MEMORY_ENTRIES = 1000
    _MAX_SQLITE_ENTRIES = 5000
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
    def _extract_template(cls, prompt_text: str) -> Tuple[str, Dict[str, str]]:
        """Extracts file paths and code identifiers in one pass for template reuse."""
        norm = cls._normalize_prompt(prompt_text)
        slots: Dict[str, str] = {}
        val_to_slot: Dict[str, str] = {}
        file_idx = 0
        sym_idx = 0

        def repl_any(m: re.Match) -> str:
            nonlocal file_idx, sym_idx
            file_val = m.group("file")
            if file_val:
                if file_val.lower() in _BRAND_PROPER_NOUNS:
                    return file_val
                if file_val not in val_to_slot:
                    slot = f"<FILE_{file_idx}>"
                    file_idx += 1
                    val_to_slot[file_val] = slot
                    slots[slot] = file_val
                return val_to_slot[file_val]
            sym_val = m.group("bt_sym") or m.group("sym")
            if sym_val:
                # Only ignore proper nouns when NOT explicitly backticked
                if not m.group("bt_sym") and sym_val.lower() in _BRAND_PROPER_NOUNS:
                    return sym_val
                if sym_val not in val_to_slot:
                    slot = f"<SYM_{sym_idx}>"
                    sym_idx += 1
                    val_to_slot[sym_val] = slot
                    slots[slot] = sym_val
                return val_to_slot[sym_val]
            return m.group(0)

        templated = _COMBINED_SLOT_RE.sub(repl_any, norm)
        parts = re.split(r"(<(?:FILE|SYM)_\d+>)", templated)
        normalized_tmpl = "".join(p if _UNRESOLVED_SLOT_RE.fullmatch(p) else p.lower() for p in parts)
        return normalized_tmpl, slots

    @classmethod
    def _to_template_sexpr(cls, distilled_text: str, slots: Dict[str, str]) -> Optional[str]:
        """Replaces extracted slot values in distilled S-Expression with placeholders using word boundaries."""
        if not slots:
            return None
        tmpl = distilled_text.strip()
        replaced_count = 0
        for slot, val in sorted(slots.items(), key=lambda kv: len(kv[1]), reverse=True):
            pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(val)}(?![A-Za-z0-9_])")
            tmpl_next, n_sub = pat.subn(slot, tmpl)
            if n_sub > 0:
                tmpl = tmpl_next
                replaced_count += 1
        # Require all extracted slots to be bound in template_sexpr so omitted slots never collide
        return tmpl if replaced_count == len(slots) else None

    @classmethod
    def _instantiate_template(cls, template_sexpr: str, slots: Dict[str, str]) -> Optional[str]:
        """Substitutes current prompt's slots back into a cached template S-Expression in a single pass."""
        if not template_sexpr:
            return None
        out = _UNRESOLVED_SLOT_RE.sub(lambda m: slots.get(m.group(0), m.group(0)), template_sexpr)
        if _UNRESOLVED_SLOT_RE.search(out):
            return None
        return out

    @classmethod
    def _get_db_path(cls) -> str:
        if cls._db_path:
            return cls._db_path
        data_dir = resolve_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "metrics.db")

    @classmethod
    def _init_db(cls, conn: sqlite3.Connection):
        db_path = cls._get_db_path()
        with cls._lock:
            if db_path in cls._initialized_dbs and os.path.exists(db_path):
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS distillation_templates (
                    template_hash TEXT PRIMARY KEY,
                    template_prompt TEXT,
                    template_sexpr TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0
                );
            """)
            conn.commit()
            cls._initialized_dbs.add(db_path)

    @classmethod
    def get(cls, prompt_text: str) -> Optional[str]:
        """Retrieves cached distillation S-Expression via exact or parametric template lookup."""
        if not prompt_text or not prompt_text.strip():
            return None

        h = cls._hash_prompt(prompt_text)

        with cls._lock:
            # 1. Check exact in-memory LRU
            if h in cls._memory_cache:
                cls._hits += 1
                distilled = cls._memory_cache.pop(h)
                cls._memory_cache[h] = distilled
                return distilled

        # 2. Check SQLite persistent exact cache
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
                        cls._memory_cache.popitem(last=False)

                return distilled_text
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        # 3. Check parametric template cache (L1 memory + L2 SQLite)
        tmpl_prompt, slots = cls._extract_template(prompt_text)
        tmpl_hash = hashlib.sha256(tmpl_prompt.encode("utf-8")).hexdigest() if slots else ""

        if tmpl_hash:
            with cls._lock:
                tmpl_sexpr = cls._template_memory_cache.get(tmpl_hash)
                if tmpl_sexpr is not None:
                    cls._template_memory_cache.move_to_end(tmpl_hash)
            if tmpl_sexpr:
                instantiated = cls._instantiate_template(tmpl_sexpr, slots)
                if instantiated:
                    cls.set(prompt_text, instantiated, provider="template_cache", sync_remote=False, store_template=False)
                    with cls._lock:
                        cls._hits += 1
                    return instantiated

            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                cls._init_db(conn)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT template_sexpr, hit_count FROM distillation_templates WHERE template_hash = ?",
                    (tmpl_hash,)
                )
                row = cursor.fetchone()
                if row:
                    tmpl_sexpr_db, hit_count = row[0], row[1]
                    instantiated = cls._instantiate_template(tmpl_sexpr_db, slots)
                    if instantiated:
                        cursor.execute(
                            "UPDATE distillation_templates SET hit_count = ? WHERE template_hash = ?",
                            (hit_count + 1, tmpl_hash)
                        )
                        conn.commit()
                        with cls._lock:
                            cls._template_memory_cache[tmpl_hash] = tmpl_sexpr_db
                            if len(cls._template_memory_cache) > cls._MAX_MEMORY_ENTRIES:
                                cls._template_memory_cache.popitem(last=False)
                        cls.set(prompt_text, instantiated, provider="template_cache", sync_remote=False, store_template=False)
                        with cls._lock:
                            cls._hits += 1
                        return instantiated
            except Exception:
                pass
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # 4. Check L3 Shared Team Cache (exact, then parametric template)
        try:
            from src.team_cache import TeamCache
            team_val = TeamCache.get("PROMPT", prompt_text)
            if team_val:
                cls.set(prompt_text, team_val, provider="team_cache", sync_remote=False)
                with cls._lock:
                    cls._hits += 1
                return team_val
            if tmpl_hash:
                team_tmpl = TeamCache.get("PROMPT_TMPL", tmpl_prompt)
                instantiated = cls._instantiate_template(team_tmpl, slots) if team_tmpl else None
                if instantiated:
                    cls.set(prompt_text, instantiated, provider="team_template_cache", sync_remote=False)
                    with cls._lock:
                        cls._hits += 1
                    return instantiated
        except Exception:
            pass

        with cls._lock:
            cls._misses += 1
        return None

    @classmethod
    def set(
        cls,
        prompt_text: str,
        distilled_text: str,
        provider: str = "deepseek",
        sync_remote: bool = True,
        store_template: bool = True,
    ) -> None:
        """Stores a distillation result and parametric template into memory, SQLite, and Team Cache."""
        if not prompt_text or not distilled_text or not distilled_text.strip():
            return

        h = cls._hash_prompt(prompt_text)
        norm_prompt = cls._normalize_prompt(prompt_text)
        clean_distilled = distilled_text.strip()
        now_iso = datetime.now().isoformat()

        tmpl_prompt = ""
        slots: Dict[str, str] = {}
        tmpl_sexpr: Optional[str] = None
        tmpl_hash = ""
        if store_template:
            tmpl_prompt, slots = cls._extract_template(prompt_text)
            tmpl_sexpr = cls._to_template_sexpr(clean_distilled, slots) if slots else None
            tmpl_hash = hashlib.sha256(tmpl_prompt.encode("utf-8")).hexdigest() if tmpl_sexpr else ""

        with cls._lock:
            cls._memory_cache[h] = clean_distilled
            cls._memory_cache.move_to_end(h)
            if len(cls._memory_cache) > cls._MAX_MEMORY_ENTRIES:
                cls._memory_cache.popitem(last=False)
            if tmpl_hash and tmpl_sexpr:
                cls._template_memory_cache[tmpl_hash] = tmpl_sexpr
                cls._template_memory_cache.move_to_end(tmpl_hash)
                if len(cls._template_memory_cache) > cls._MAX_MEMORY_ENTRIES:
                    cls._template_memory_cache.popitem(last=False)

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
            """, (h, norm_prompt[:500], clean_distilled, provider, now_iso))
            if store_template and tmpl_hash and tmpl_sexpr:
                cursor.execute("""
                    INSERT INTO distillation_templates (template_hash, template_prompt, template_sexpr, provider, created_at, hit_count)
                    VALUES (?, ?, ?, ?, ?, 0)
                    ON CONFLICT(template_hash) DO UPDATE SET
                        template_sexpr = excluded.template_sexpr,
                        provider = excluded.provider
                """, (tmpl_hash, tmpl_prompt[:500], tmpl_sexpr, provider, now_iso))
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
                TeamCache.put("PROMPT", prompt_text, clean_distilled, tokens_saved=45)
                if tmpl_hash and tmpl_sexpr:
                    TeamCache.put("PROMPT_TMPL", tmpl_prompt, tmpl_sexpr, tokens_saved=45)
            except Exception:
                pass

    @classmethod
    def clear(cls) -> None:
        """Clears both in-memory and persistent SQLite cache."""
        with cls._lock:
            cls._memory_cache.clear()
            cls._template_memory_cache.clear()
            cls._hits = 0
            cls._misses = 0

        db_path = cls._get_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cls._init_db(conn)
            conn.execute("DELETE FROM distillation_cache;")
            conn.execute("DELETE FROM distillation_templates;")
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
