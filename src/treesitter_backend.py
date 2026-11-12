"""
Optional Tree-sitter Backend for TokenSafe GraphEngine and Skeletonizer.

Strictly optional: all imports of `tree_sitter` and `tree_sitter_*` grammars are
lazy and fail-safe. When unavailable (or disabled via TOKENSAFE_TREESITTER=0),
callers fall back seamlessly to TokenSafe's zero-dependency stdlib AST/BraceLexer
engines.
"""

import importlib
import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Extension -> (module_name, loader_attr)
_LANGUAGE_SPECS: Dict[str, Tuple[str, str]] = {
    ".py": ("tree_sitter_python", "language"),
    ".js": ("tree_sitter_javascript", "language"),
    ".jsx": ("tree_sitter_javascript", "language"),
    ".mjs": ("tree_sitter_javascript", "language"),
    ".cjs": ("tree_sitter_javascript", "language"),
    ".ts": ("tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tree_sitter_typescript", "language_tsx"),
    ".go": ("tree_sitter_go", "language"),
    ".rs": ("tree_sitter_rust", "language"),
    ".java": ("tree_sitter_java", "language"),
    ".cs": ("tree_sitter_c_sharp", "language"),
    ".c": ("tree_sitter_c", "language"),
    ".h": ("tree_sitter_c", "language"),
    ".cpp": ("tree_sitter_cpp", "language"),
    ".cc": ("tree_sitter_cpp", "language"),
    ".cxx": ("tree_sitter_cpp", "language"),
    ".hpp": ("tree_sitter_cpp", "language"),
    ".php": ("tree_sitter_php", "language_php"),
    ".kt": ("tree_sitter_kotlin", "language"),
    ".kts": ("tree_sitter_kotlin", "language"),
    ".swift": ("tree_sitter_swift", "language"),
}

_CONTAINER_NODE_TYPES = {
    "class_definition",
    "class_declaration",
    "class_specifier",
    "struct_item",
    "struct_specifier",
    "struct_declaration",
    "impl_item",
    "trait_item",
    "trait_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "record_declaration",
    "enum_declaration",
    "enum_item",
    "protocol_declaration",
    "extension_declaration",
    "namespace_definition",
    "namespace_declaration",
}

_FUNC_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
    "constructor_declaration",
    "destructor_declaration",
    "arrow_function",
    "function_expression",
    "function",
    "operator_declaration",
    "init_declaration",
    "deinit_declaration",
    "secondary_constructor",
    "anonymous_function",
}

_IMPORT_NODE_TYPES = {
    "import_statement",
    "import_from_statement",
    "import_declaration",
    "use_declaration",
    "using_directive",
    "preproc_include",
    "namespace_use_declaration",
    "import",
}

_CALL_NODE_TYPES = {
    "call",
    "call_expression",
    "method_invocation",
    "invocation_expression",
    "function_call_expression",
    "member_call_expression",
    "scoped_call_expression",
    "macro_invocation",
    "object_creation_expression",
    "new_expression",
}

_SKIP_CALL_NAMES = {
    "if", "for", "while", "switch", "catch", "return", "throw", "sizeof",
    "typeof", "instanceof", "new", "delete", "await", "yield", "import",
    "require", "print", "len", "str", "int", "float", "bool", "list",
    "dict", "set", "tuple", "div", "span", "button", "input", "label",
    "form", "svg", "path", "true", "false", "null", "undefined", "None",
    "True", "False",
}


class TreeSitterBackend:
    """Lazy, cached wrapper around optional `tree_sitter` language parsers."""

    _ts_mod: Any = None
    _ts_checked: bool = False
    _parsers: Dict[str, Any] = {}
    _unavailable_exts: Set[str] = set()

    @classmethod
    def is_enabled(cls) -> bool:
        flag = os.environ.get("TOKENSAFE_TREESITTER", "auto").strip().lower()
        return flag not in ("0", "false", "off", "no", "disabled")

    @classmethod
    def is_available(cls) -> bool:
        if not cls.is_enabled():
            return False
        if not cls._ts_checked:
            cls._ts_checked = True
            try:
                cls._ts_mod = importlib.import_module("tree_sitter")
            except Exception:
                cls._ts_mod = None
        return cls._ts_mod is not None

    @classmethod
    def get_parser(cls, ext: str) -> Optional[Any]:
        ext = ext.lower()
        if not ext.startswith("."):
            ext = f".{ext}" if ext else ""
        if not cls.is_available() or ext not in _LANGUAGE_SPECS or ext in cls._unavailable_exts:
            return None
        if ext in cls._parsers:
            return cls._parsers[ext]

        mod_name, fn_name = _LANGUAGE_SPECS[ext]
        try:
            lang_mod = importlib.import_module(mod_name)
            lang_ptr = getattr(lang_mod, fn_name)()
            lang_obj = cls._ts_mod.Language(lang_ptr)
            parser = cls._ts_mod.Parser(lang_obj)
            cls._parsers[ext] = parser
            return parser
        except Exception:
            cls._unavailable_exts.add(ext)
            return None

    @classmethod
    def supports_ext(cls, ext: str) -> bool:
        return cls.get_parser(ext) is not None

    @classmethod
    def supported_languages(cls) -> Set[str]:
        if not cls.is_available():
            return set()
        return {ext for ext in _LANGUAGE_SPECS if cls.get_parser(ext) is not None}

    @staticmethod
    def _node_text(node: Any, code_bytes: bytes) -> str:
        return code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @classmethod
    def _find_identifier_in_declarator(cls, node: Any, code_bytes: bytes) -> Optional[str]:
        if node is None:
            return None
        if node.type in (
            "identifier", "type_identifier", "field_identifier",
            "property_identifier", "simple_identifier", "name",
        ):
            return cls._node_text(node, code_bytes)
        if node.type == "qualified_identifier":
            # e.g. C++ ClassName::method -> take last identifier or full
            parts = [cls._find_identifier_in_declarator(c, code_bytes) for c in node.children]
            parts = [p for p in parts if p]
            return parts[-1] if parts else None
        if node.type in ("function_declarator", "pointer_declarator", "reference_declarator"):
            decl = node.child_by_field_name("declarator")
            if decl is not None:
                res = cls._find_identifier_in_declarator(decl, code_bytes)
                if res:
                    return res
        for child in node.children:
            if child.type in (
                "identifier", "type_identifier", "field_identifier",
                "property_identifier", "simple_identifier", "name",
                "function_declarator", "qualified_identifier",
            ):
                res = cls._find_identifier_in_declarator(child, code_bytes)
                if res:
                    return res
        return None

    @classmethod
    def _extract_symbol_name(cls, node: Any, code_bytes: bytes) -> Optional[str]:
        # Rust impl Foo / impl Trait for Foo
        if node.type == "impl_item":
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                return cls._find_identifier_in_declarator(type_node, code_bytes)
            for c in node.children:
                if c.type in ("type_identifier", "generic_type"):
                    return cls._find_identifier_in_declarator(c, code_bytes)

        # Go type_declaration -> type_spec
        if node.type == "type_declaration":
            for c in node.children:
                if c.type == "type_spec":
                    name_n = c.child_by_field_name("name")
                    if name_n is not None:
                        return cls._node_text(name_n, code_bytes)

        # Arrow/function assigned to variable_declarator: const Foo = (...) => {}
        if node.type in ("arrow_function", "function_expression", "function"):
            parent = node.parent
            while parent is not None and parent.type in ("call_expression", "arguments", "parenthesized_expression"):
                # Handle React.memo((...) => ...) or useCallback((...) => ...)
                parent = parent.parent
            if parent is not None and parent.type == "variable_declarator":
                name_n = parent.child_by_field_name("name")
                if name_n is not None:
                    return cls._node_text(name_n, code_bytes)
            if parent is not None and parent.type == "pair":
                key_n = parent.child_by_field_name("key")
                if key_n is not None:
                    return cls._node_text(key_n, code_bytes).strip("\"'")

        # Standard named field (`name`)
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return cls._find_identifier_in_declarator(name_node, code_bytes)

        # C/C++ declarator
        decl_node = node.child_by_field_name("declarator")
        if decl_node is not None:
            return cls._find_identifier_in_declarator(decl_node, code_bytes)

        # Fallback: first direct identifier/name child
        for child in node.children:
            if child.type in (
                "identifier", "type_identifier", "field_identifier",
                "property_identifier", "simple_identifier", "name",
            ):
                return cls._node_text(child, code_bytes)
        return None

    @classmethod
    def _find_body_node(cls, node: Any) -> Optional[Any]:
        body = node.child_by_field_name("body")
        if body is not None:
            return body
        for child in node.children:
            if child.type in (
                "block", "statement_block", "compound_statement",
                "function_body", "declaration_list",
            ):
                return child
        return None

    @classmethod
    def _extract_call_name(cls, node: Any, code_bytes: bytes) -> Optional[str]:
        if node.type == "method_invocation":
            name_n = node.child_by_field_name("name")
            if name_n is not None:
                return cls._node_text(name_n, code_bytes)
        if node.type == "member_call_expression":
            name_n = node.child_by_field_name("name")
            if name_n is not None:
                return cls._node_text(name_n, code_bytes)

        fn_node = node.child_by_field_name("function") or node.child_by_field_name("name")
        if fn_node is None and node.children:
            fn_node = node.children[0]
        if fn_node is None:
            return None

        text = cls._node_text(fn_node, code_bytes).strip()
        # Strip trailing generics/arguments and take leaf identifier after ., ::, ->
        text = re.sub(r"<[^>]*>$", "", text)
        leaf = re.split(r"(?:\.|::|->|\?\.)", text)[-1].strip()
        if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", leaf) and leaf not in _SKIP_CALL_NAMES:
            return leaf
        return None

    @classmethod
    def _extract_imports_from_node(cls, node: Any, code_bytes: bytes, ext: str) -> Tuple[List[str], List[str]]:
        mods: List[str] = []
        syms: List[str] = []
        raw = cls._node_text(node, code_bytes).strip()

        if ext == ".py":
            if node.type == "import_statement":
                for c in node.children:
                    if c.type in ("dotted_name", "aliased_import"):
                        t = cls._node_text(c, code_bytes).split(" as ")[0].strip()
                        if t:
                            mods.append(t)
            elif node.type == "import_from_statement":
                m = re.match(r"^from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)$", raw, re.DOTALL)
                if m:
                    mods.append(m.group(1).strip())
                    for item in m.group(2).replace("(", "").replace(")", "").split(","):
                        sym = item.strip().split(" as ")[0].strip()
                        if sym and sym != "*":
                            syms.append(sym)
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            for m in re.finditer(r"""['"]([^'"]+)['"]""", raw):
                mods.append(m.group(1))
            m_named = re.search(r"\{([^}]+)\}", raw)
            if m_named:
                for item in m_named.group(1).split(","):
                    s = item.strip().split(" as ")[0].strip()
                    if s:
                        syms.append(s)
        elif ext == ".go":
            for m in re.finditer(r'"([^"]+)"', raw):
                mods.append(m.group(1))
        elif ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp"):
            m = re.search(r'[<"]([^>"]+)[>"]', raw)
            if m:
                mods.append(m.group(1))
        else:
            # Rust use, Java/Kotlin import, C# using, PHP use, Swift import
            cleaned = re.sub(r"^(?:pub\s+)?(?:use|import|using|namespace)\s+", "", raw)
            cleaned = cleaned.rstrip(";").strip()
            if cleaned:
                mods.append(cleaned)
        return mods, syms

    @classmethod
    def extract_graph_data(cls, code: str, ext: str, rel_path: str) -> Optional[Dict[str, Any]]:
        parser = cls.get_parser(ext)
        if parser is None:
            return None

        code_bytes = code.encode("utf-8", errors="replace")
        try:
            tree = parser.parse(code_bytes)
        except Exception:
            return None

        lines = code.splitlines()
        imports: List[str] = []
        imported_symbols: List[str] = []
        symbols: List[Dict[str, Any]] = []
        seen_keys: Set[str] = set()
        calls: List[Tuple[str, str]] = []

        def walk(node: Any, container_stack: List[str], current_fn_key: Optional[str]):
            ntype = node.type

            if ntype in _IMPORT_NODE_TYPES:
                mods, syms = cls._extract_imports_from_node(node, code_bytes, ext)
                imports.extend(mods)
                imported_symbols.extend(syms)
                return

            # Go `type Foo struct { ... }` / `type Foo interface { ... }`
            is_go_type = (
                ext == ".go"
                and ntype == "type_declaration"
                and any(c.type == "type_spec" for c in node.children)
            )

            if ntype in _CONTAINER_NODE_TYPES or is_go_type:
                name = cls._extract_symbol_name(node, code_bytes)
                if name:
                    qual = "::".join(container_stack + [name]) if container_stack else name
                    sym_key = f"{rel_path}::{qual}"
                    if sym_key not in seen_keys:
                        seen_keys.add(sym_key)
                        l_start = node.start_point[0] + 1
                        l_end = max(l_start, node.end_point[0] + 1)
                        sig = lines[l_start - 1].strip() if 0 < l_start <= len(lines) else name
                        symbols.append({
                            "sym_key": sym_key,
                            "name": name,
                            "qualified_name": qual,
                            "kind": "class",
                            "line_start": l_start,
                            "line_end": l_end,
                            "signature": sig,
                        })
                    next_stack = container_stack + [name]
                else:
                    next_stack = container_stack

                for child in node.children:
                    walk(child, next_stack, current_fn_key)
                return

            if ntype in _FUNC_NODE_TYPES:
                name = cls._extract_symbol_name(node, code_bytes)
                next_fn_key = current_fn_key
                if name:
                    qual = "::".join(container_stack + [name]) if container_stack else name
                    sym_key = f"{rel_path}::{qual}"
                    if sym_key not in seen_keys:
                        seen_keys.add(sym_key)
                        l_start = node.start_point[0] + 1
                        l_end = max(l_start, node.end_point[0] + 1)
                        # Use the variable declaration line for arrow functions if applicable
                        sig_line = l_start
                        if ntype in ("arrow_function", "function_expression") and node.parent is not None:
                            sig_line = min(l_start, node.parent.start_point[0] + 1)
                        sig = lines[sig_line - 1].strip() if 0 < sig_line <= len(lines) else name
                        symbols.append({
                            "sym_key": sym_key,
                            "name": name,
                            "qualified_name": qual,
                            "kind": "function",
                            "line_start": l_start,
                            "line_end": l_end,
                            "signature": sig,
                        })
                    next_fn_key = sym_key

                for child in node.children:
                    walk(child, container_stack, next_fn_key)
                return

            if ntype in _CALL_NODE_TYPES and current_fn_key:
                callee = cls._extract_call_name(node, code_bytes)
                if callee:
                    calls.append((current_fn_key, callee))

            for child in node.children:
                walk(child, container_stack, current_fn_key)

        walk(tree.root_node, [], None)
        return {
            "imports": imports,
            "imported_symbols": imported_symbols,
            "symbols": symbols,
            "calls": calls,
        }

    @classmethod
    def skeletonize_braces(
        cls,
        code: str,
        ext: str,
        keep_symbols: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """
        Tree-sitter brace-language skeletonizer: replaces non-kept function/method
        bodies with `{ /* ... */ }` while preserving multi-line signatures,
        Allman-style braces, classes/structs/impls, and control-flow blocks.
        """
        ext = ext.lower()
        if not ext.startswith("."):
            ext = f".{ext}" if ext else ""
        if ext == ".py":
            return None
        parser = cls.get_parser(ext)
        if parser is None:
            return None

        code_bytes = code.encode("utf-8", errors="replace")
        try:
            tree = parser.parse(code_bytes)
        except Exception:
            return None

        replacements: List[Tuple[int, int, bytes]] = []
        comment_token = "# ..." if ext == ".py" else "/* ... */"
        folded_body = f"{{ {comment_token} }}".encode("utf-8")

        def walk(node: Any, container_stack: List[str], in_kept_scope: bool = False):
            ntype = node.type

            if ntype in _CONTAINER_NODE_TYPES:
                cname = cls._extract_symbol_name(node, code_bytes)
                next_stack = container_stack + [cname] if cname else container_stack
                for child in node.children:
                    walk(child, next_stack, in_kept_scope)
                return

            if ntype in _FUNC_NODE_TYPES:
                name = cls._extract_symbol_name(node, code_bytes)
                qual_dot = ".".join(container_stack + [name]) if (container_stack and name) else name
                qual_col = "::".join(container_stack + [name]) if (container_stack and name) else name
                body = cls._find_body_node(node)
                is_target_kept = (
                    keep_symbols is not None
                    and (
                        (name and name in keep_symbols)
                        or (qual_dot and qual_dot in keep_symbols)
                        or (qual_col and qual_col in keep_symbols)
                    )
                )
                if body is not None:
                    body_bytes = code_bytes[body.start_byte:body.end_byte]
                    if body_bytes.startswith(b"{") and body_bytes.endswith(b"}"):
                        if is_target_kept:
                            for child in node.children:
                                walk(child, container_stack, True)
                            return
                        # Inside a kept function, leave unnamed inline callbacks intact
                        if in_kept_scope and not name:
                            for child in node.children:
                                walk(child, container_stack, True)
                            return
                        replacements.append((body.start_byte, body.end_byte, folded_body))
                        return
                for child in node.children:
                    walk(child, container_stack, in_kept_scope or is_target_kept)
                return

            for child in node.children:
                walk(child, container_stack, in_kept_scope)

        walk(tree.root_node, [], False)
        if not replacements:
            return code

        # Sort and deduplicate non-overlapping spans
        replacements.sort(key=lambda r: (r[0], -r[1]))
        out_parts: List[bytes] = []
        cursor = 0
        for start, end, rep in replacements:
            if start < cursor:
                continue
            out_parts.append(code_bytes[cursor:start])
            out_parts.append(rep)
            cursor = end
        out_parts.append(code_bytes[cursor:])
        return b"".join(out_parts).decode("utf-8", errors="replace")
