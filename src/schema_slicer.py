"""Schema-aware slicer for heavy declarative files that break AST code tooling.

Opening a 5,000-line OpenAPI spec, Prisma schema, GraphQL SDL or SQL migration
dump costs 20k-50k tokens; generic AST skeletonization does not apply to them.
SchemaSlicer returns a compact overview when no target is given, and a targeted
slice (the requested entry + 1-hop references) when one is:

- OpenAPI/Swagger (JSON + YAML): one path + its $ref schema closure, or one schema.
- Prisma: one model block + 1-hop relation targets.
- GraphQL SDL: one type block + 1-hop field types.
- SQL: DDL statements only (CREATE TABLE/INDEX/ALTER/TYPE) — INSERT/UPDATE
  history is dropped; a target table filters further.

Standard library only. `target` semantics follow the `symbol` argument of
smart_read_file so agents use one familiar entry point.
"""

import json
import os
import re

from src.tokenizer import count_tokens
from typing import Any, Dict, List, Optional, Tuple

_MAX_REF_CHAIN = 15
_MAX_SQL_BYTES = 4 * 1024 * 1024


class SchemaSlicer:
    """Detects schema files and produces targeted, token-cheap slices."""

    @classmethod
    def detect(cls, path: str) -> Optional[str]:
        """Returns the schema kind for `path` (openapi|prisma|graphql|sql) or None."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".prisma":
            return "prisma"
        if ext in (".graphql", ".gql"):
            return "graphql"
        if ext == ".sql":
            return "sql"
        if ext == ".json":
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(4096)
                return "openapi" if re.search(r'"(?:openapi|swagger|paths)"\s*:', head) else None
            except Exception:
                return None
        if ext in (".yaml", ".yml"):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(4096)
                return "openapi" if re.search(r"^\s*(openapi|swagger)\s*:", head, re.MULTILINE) else None
            except Exception:
                return None
        return None

    @classmethod
    def slice_file(cls, path: str, target: str = "") -> Tuple[str, Dict[str, Any]]:
        """Returns (sliced_text, info). Empty `target` yields a compact overview.

        Never EXPANDS: when the slice is not smaller than the raw file (tiny
        toy schemas — QA sweep found 14-token graphql fixtures growing to 23),
        the raw text is returned unchanged, same honest pass-through as
        DiffSlicer."""
        raw_text = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
        except OSError:
            pass
        kind = cls.detect(path)
        sliced = ""
        if kind == "openapi":
            sliced, info = cls._slice_openapi(path, target.strip())
        elif kind == "prisma":
            sliced, info = cls._slice_prisma(path, target.strip())
        elif kind == "graphql":
            sliced, info = cls._slice_graphql(path, target.strip())
        elif kind == "sql":
            sliced, info = cls._slice_sql(path, target.strip())
        else:
            return "", {"kind": None}
        if not target.strip() and count_tokens(sliced) >= count_tokens(raw_text):
            return raw_text, info  # overview pass-through; targeted slices keep precision semantics
        return sliced, info

    # ------------------------------------------------------------------ OpenAPI

    @classmethod
    def _slice_openapi(cls, path: str, target: str) -> Tuple[str, Dict[str, Any]]:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".yaml", ".yml"):
            return cls._slice_openapi_yaml(path, target)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                spec = json.load(f)
        except Exception as e:
            return f"[TokenSafe SchemaSlicer] Cannot parse OpenAPI JSON: {e}", {"kind": "openapi", "error": True}

        def schema_pool() -> Dict[str, Any]:
            comps = spec.get("components") or {}
            return (comps.get("schemas") or {}) or (spec.get("definitions") or {})

        def resolve_refs(node: Any, pool: Dict[str, Any], acc: Dict[str, Any], depth: int = 0) -> None:
            if depth > _MAX_REF_CHAIN or len(acc) >= _MAX_REF_CHAIN:
                return
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/"):
                    name = ref.rsplit("/", 1)[-1]
                    if name not in acc and name in pool:
                        acc[name] = pool[name]
                        resolve_refs(pool[name], pool, acc, depth + 1)
                for v in node.values():
                    resolve_refs(v, pool, acc, depth + 1)
            elif isinstance(node, list):
                for v in node:
                    resolve_refs(v, pool, acc, depth + 1)

        if target.startswith("/"):
            item = (spec.get("paths") or {}).get(target)
            if item is None:
                return cls._openapi_overview(spec, missing=target)
            pool = schema_pool()
            acc: Dict[str, Any] = {}
            resolve_refs(item, pool, acc)
            out = [f"# OpenAPI path: {target}", json.dumps({target: item}, indent=2, ensure_ascii=False)]
            if acc:
                out.append("\n# Referenced schemas ($ref closure)")
                out.append(json.dumps(acc, indent=2, ensure_ascii=False))
            return "\n".join(out), {"kind": "openapi", "mode": "path", "refs": list(acc)}
        if target:
            pool = schema_pool()
            if target in pool:
                acc: Dict[str, Any] = {}
                resolve_refs(pool[target], pool, acc)
                body = {"name": target, **(pool[target] if isinstance(pool[target], dict) else {"schema": pool[target]})}
                out = [f"# OpenAPI schema: {target}", json.dumps(body, indent=2, ensure_ascii=False)]
                others = {k: v for k, v in acc.items() if k != target}
                if others:
                    out.append("\n# Referenced schemas ($ref closure)")
                    out.append(json.dumps(others, indent=2, ensure_ascii=False))
                return "\n".join(out), {"kind": "openapi", "mode": "schema", "refs": list(others)}
            # Try a method-scoped path: "/users get" or "/users:get"
            parts = re.split(r"[\s:]+", target, maxsplit=1)
            if len(parts) == 2 and parts[0].startswith("/") and parts[1].lower() in ("get", "post", "put", "patch", "delete", "options", "head"):
                item = (spec.get("paths") or {}).get(parts[0]) or {}
                op = item.get(parts[1].lower())
                if op is not None:
                    pool = schema_pool()
                    acc: Dict[str, Any] = {}
                    resolve_refs(op, pool, acc)
                    out = [f"# OpenAPI operation: {parts[1].upper()} {parts[0]}", json.dumps({parts[1].lower(): op}, indent=2, ensure_ascii=False)]
                    if acc:
                        out.append("\n# Referenced schemas ($ref closure)")
                        out.append(json.dumps(acc, indent=2, ensure_ascii=False))
                    return "\n".join(out), {"kind": "openapi", "mode": "operation", "refs": list(acc)}
            return cls._openapi_overview(spec, missing=target)
        return cls._openapi_overview(spec)

    @classmethod
    def _openapi_overview(cls, spec: Dict[str, Any], missing: str = "") -> Tuple[str, Dict[str, Any]]:
        lines = [f"# OpenAPI {spec.get('openapi') or spec.get('swagger') or ''} — overview"]
        if missing:
            lines.append(f"# (target '{missing}' not found — showing the spec index)")
        paths = spec.get("paths") or {}
        lines.append(f"\n## Paths ({len(paths)})")
        for p, item in list(paths.items())[:120]:
            methods = [m.upper() for m in (item or {}) if m in ("get", "post", "put", "patch", "delete", "options", "head")]
            lines.append(f"  {'/'.join(methods) or '-'} {p}")
        pool = (spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}
        names = list(pool.keys())
        lines.append(f"\n## Schemas ({len(names)})")
        lines.append("  " + ", ".join(names[:150]))
        lines.append("\nSlice a specific entry: symbol='/users/{id}' (path), '/users get' (operation) or 'User' (schema).")
        return "\n".join(lines), {"kind": "openapi", "mode": "overview"}

    @classmethod
    def _slice_openapi_yaml(cls, path: str, target: str) -> Tuple[str, Dict[str, Any]]:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Top-level paths block: lines matching "  /path:" under "paths:"
        in_paths = False
        blocks: Dict[str, List[str]] = {}
        current: Optional[str] = None
        for ln in lines:
            if re.match(r"^paths\s*:", ln):
                in_paths = True
                continue
            if in_paths:
                if re.match(r"^[A-Za-z_#-]", ln):  # next top-level key ends paths
                    in_paths = False
                    current = None
                else:
                    m = re.match(r"^  (\s*/[^\s:]+)\s*:", ln)
                    if m:
                        current = m.group(1)
                        blocks[current] = [ln]
                    elif current:
                        blocks[current].append(ln)
        if not target:
            lines_out = [f"# OpenAPI (YAML) — overview", "", "## Paths"]
            for p in list(blocks)[:120]:
                lines_out.append(f"  {p.rstrip(':')}")
            lines_out.append("\nSlice a specific path: symbol='/users/{id}'.")
            return "\n".join(lines_out), {"kind": "openapi", "mode": "overview"}
        norm = target if target.startswith("/") else "/" + target
        blk = blocks.get(norm) or blocks.get(target.rstrip(":"))
        if not blk:
            return "\n".join([f"# target '{target}' not found", "## Paths"] + [f"  {p.rstrip(':')}" for p in list(blocks)[:120]]), {"kind": "openapi", "mode": "missing"}
        return f"# OpenAPI path: {norm}\n" + "".join(blk), {"kind": "openapi", "mode": "path"}

    # ------------------------------------------------------------------ Prisma

    @classmethod
    def _slice_prisma(cls, path: str, target: str) -> Tuple[str, Dict[str, Any]]:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        block_re = re.compile(r"^(model|enum|type)\s+(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
        blocks: Dict[str, Tuple[str, str]] = {}
        order: List[str] = []
        for m in block_re.finditer(src):
            name = m.group(2)
            blocks[name] = (m.group(1), m.group(0))
            order.append(name)
        if not target:
            lines = [f"# Prisma schema — overview ({len(blocks)} definitions)", ""]
            for name in order[:150]:
                kind_name, body = blocks[name]
                fields = len(re.findall(r"^\s{2,}\w+", body, re.MULTILINE))
                lines.append(f"  {kind_name} {name} ({fields} fields)")
            lines.append("\nSlice a model: symbol='User'.")
            return "\n".join(lines), {"kind": "prisma", "mode": "overview"}
        if target not in blocks:
            return f"# target '{target}' not found. Definitions: {', '.join(order[:100])}", {"kind": "prisma", "mode": "missing"}
        kind_name, body = blocks[target]
        out = [f"# Prisma {kind_name} {target}", body]
        # 1-hop: capitalized field types that are themselves defined blocks
        refs = set()
        for fm in re.finditer(r"^\s{2,}\w+\??\s+([A-Z]\w*)(?:\[\])?(?:\s|$)", body, re.MULTILINE):
            if fm.group(1) in blocks and fm.group(1) != target:
                refs.add(fm.group(1))
        if refs:
            out.append("\n# 1-hop related definitions")
            for r in sorted(refs):
                out.append(blocks[r][1])
        return "\n".join(out), {"kind": "prisma", "mode": "model", "refs": sorted(refs)}

    # ------------------------------------------------------------------ GraphQL

    @classmethod
    def _slice_graphql(cls, path: str, target: str) -> Tuple[str, Dict[str, Any]]:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        src = re.sub(r'"""[\s\S]*?"""', '""', src)  # drop block descriptions for overview scanning
        block_re = re.compile(r"^(type|input|interface|enum)\s+(\w+)([^{}]*?)\{(.*?)\}", re.MULTILINE | re.DOTALL)
        blocks: Dict[str, Tuple[str, str]] = {}
        order: List[str] = []
        for m in block_re.finditer(src):
            name = m.group(2)
            blocks[name] = (m.group(1), m.group(0))
            order.append(name)
        for m in re.finditer(r"^(scalar|union)\s+(\w+)([^\n]*)", src, re.MULTILINE):
            name = m.group(2)
            if name not in blocks:
                blocks[name] = (m.group(1), m.group(0).strip())
                order.append(name)
        if not target:
            lines = [f"# GraphQL SDL — overview ({len(blocks)} definitions)", ""]
            for name in order[:150]:
                kind_name, _ = blocks[name]
                lines.append(f"  {kind_name} {name}")
            lines.append("\nSlice a type: symbol='User'.")
            return "\n".join(lines), {"kind": "graphql", "mode": "overview"}
        if target not in blocks:
            return f"# target '{target}' not found. Definitions: {', '.join(order[:100])}", {"kind": "graphql", "mode": "missing"}
        kind_name, body = blocks[target]
        out = [f"# GraphQL {kind_name} {target}", body]
        refs = set()
        for fm in re.finditer(r":\s*\[?([A-Z]\w*)[!\]]*(?:\s|$)", body):
            if fm.group(1) in blocks and fm.group(1) != target:
                refs.add(fm.group(1))
        if refs:
            out.append("\n# 1-hop related types")
            for r in sorted(refs):
                out.append(blocks[r][1])
        return "\n".join(out), {"kind": "graphql", "mode": "type", "refs": sorted(refs)}

    # ------------------------------------------------------------------ SQL

    _SQL_DDL_RE = re.compile(
        r"^\s*(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|MATERIALIZED\s+VIEW|VIEW|TYPE|SCHEMA)|ALTER\s+TABLE)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _slice_sql(cls, path: str, target: str) -> Tuple[str, Dict[str, Any]]:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read(_MAX_SQL_BYTES)
        # Split into statements on ';' (string-literal safe enough for DDL scans:
        # quoted ';' inside INSERT values never appears in DDL we keep).
        statements = [s.strip() for s in src.split(";") if s.strip()]
        ddl = [s for s in statements if cls._SQL_DDL_RE.match(s)]
        if not target:
            tables = []
            for s in ddl:
                m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?([\w.]+)[\"'`]?", s, re.IGNORECASE)
                if m:
                    tables.append(m.group(1))
            lines = [f"# SQL DDL skeleton — {len(ddl)} DDL statements, {len(tables)} tables (DML history omitted)", ""]
            for t in tables[:150]:
                lines.append(f"  {t}")
            lines.append("\nSlice a table: symbol='users'.")
            return "\n".join(lines), {"kind": "sql", "mode": "overview", "tables": tables}
        t_lower = target.lower().lstrip('"\'`')
        matched = [s for s in ddl if re.search(r"[\"'`]?[\w.]*" + re.escape(t_lower) + r"[\"`]?", s, re.IGNORECASE)]
        if not matched:
            return f"# target '{target}' not found among {len(ddl)} DDL statements", {"kind": "sql", "mode": "missing"}
        return ";\n\n".join(matched) + ";", {"kind": "sql", "mode": "ddl", "statements": len(matched)}
