"""Per-file extraction with tree-sitter: outline, imports, and call references."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter as ts
import tree_sitter_language_pack as tslp


@dataclass
class Sym:
    name: str
    qualname: str
    kind: str
    start_line: int  # 1-based
    end_line: int
    start_byte: int
    end_byte: int
    signature: str = ""
    parent: str | None = None  # qualname of parent
    doc: str | None = None


@dataclass
class Call:
    name: str
    line: int  # 1-based
    byte: int
    member: bool = False  # called on a receiver: obj.name(), pkg.Name(), A::name()
    receiver: str | None = None  # receiver text when it is a plain identifier: `graph` in graph.callees()


@dataclass
class Imp:
    source: str
    items: list[str]
    line: int


@dataclass
class FileFacts:
    path: str
    language: str
    lines: int = 0
    code_lines: int = 0
    symbols: list[Sym] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    imports: list[Imp] = field(default_factory=list)
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def enclosing(self, byte: int) -> Sym | None:
        """Innermost symbol containing a byte offset."""
        best = None
        for s in self.symbols:
            if s.start_byte <= byte < s.end_byte and (best is None or s.start_byte >= best.start_byte):
                best = s
        return best


def _kind(k) -> str:
    return str(k).split(".")[-1].lower()


# Call-reference patterns for grammars whose bundled tags.scm has no `reference.call`.
_JS_CALLS = [
    "(call_expression function: (identifier) @name) @reference.call",
    "(call_expression function: (member_expression property: (property_identifier) @name)) @reference.call",
    "(new_expression constructor: (identifier) @name) @reference.call",
]
_C_CALLS = [
    "(call_expression function: (identifier) @name) @reference.call",
    "(call_expression function: (field_expression field: (field_identifier) @name)) @reference.call",
]
EXTRA_CALLS: dict[str, list[str]] = {
    "typescript": _JS_CALLS,
    "tsx": _JS_CALLS,
    "c": _C_CALLS,
    "cpp": _C_CALLS + [
        "(call_expression function: (qualified_identifier name: (identifier) @name)) @reference.call",
        "(call_expression function: (template_function name: (identifier) @name)) @reference.call",
    ],
    "swift": [
        "(call_expression (simple_identifier) @name) @reference.call",
        "(call_expression (navigation_expression suffix: (navigation_suffix suffix: (simple_identifier) @name))) @reference.call",
    ],
    "c_sharp": [
        "(invocation_expression function: (identifier) @name) @reference.call",
        "(invocation_expression function: (member_access_expression name: (identifier) @name)) @reference.call",
        "(object_creation_expression type: (identifier) @name) @reference.call",
    ],
    "csharp": [],  # alias filled below
    "bash": ["(command name: (command_name) @name) @reference.call"],
}
EXTRA_CALLS["csharp"] = EXTRA_CALLS["c_sharp"]


@lru_cache(maxsize=None)
def _tags_query(lang: str) -> ts.Query | None:
    """Bundled tags query, plus extra call patterns where needed. Bad patterns are skipped individually."""
    try:
        language = tslp.get_language(lang)
    except Exception:
        return None
    parts = []
    base = tslp.get_tags_query(lang) or ""
    if base:
        try:
            ts.Query(language, base)
            parts.append(base)
        except Exception:
            pass
    if "reference.call" not in base:
        for pat in EXTRA_CALLS.get(lang, []):
            try:
                ts.Query(language, pat)
                parts.append(pat)
            except Exception:
                pass
    return ts.Query(language, "\n".join(parts)) if parts else None


def _is_member(call_node: ts.Node, name_node: ts.Node) -> bool:
    if call_node.child_by_field_name("receiver") is not None:  # ruby, some others
        return True
    parent = name_node.parent
    return parent is not None and parent.id != call_node.id and parent.type not in ("arguments", "argument_list")


def _receiver(call_node: ts.Node, name_node: ts.Node) -> str | None:
    """Plain-identifier receiver of a member call (`mod` in mod.f()), else None."""
    r = call_node.child_by_field_name("receiver")
    if r is None:
        parent = name_node.parent
        if parent is None or parent.id == call_node.id or not parent.named_children:
            return None
        r = parent.named_children[0]
    if r.id == name_node.id or r.type != "identifier":
        return None
    return r.text.decode("utf-8", errors="replace")


def parse_file(path: str, language: str, source: bytes) -> FileFacts:
    facts = FileFacts(path=path, language=language)
    text = source.decode("utf-8", errors="replace")

    try:
        res = tslp.process(text, tslp.ProcessConfig(language=language, structure=True, imports=True))
    except Exception as e:  # grammar missing, parse failure, etc.
        facts.notes.append(f"process failed: {e}")
        return facts

    if res.metrics:
        facts.lines = res.metrics.total_lines
        facts.code_lines = res.metrics.code_lines
        facts.errors = res.metrics.error_count

    # tslp spans are byte offsets into the UTF-8 source.
    def walk(items, parent: Sym | None):
        for it in items or []:
            if not it.name:
                walk(it.children, parent)
                continue
            qual = f"{parent.qualname}.{it.name}" if parent else it.name
            s = Sym(
                name=it.name, qualname=qual, kind=_kind(it.kind),
                start_line=it.span.start_line + 1, end_line=it.span.end_line + 1,
                start_byte=it.span.start_byte, end_byte=it.span.end_byte,
                signature=(it.signature or "").strip()[:200],
                parent=parent.qualname if parent else None,
                doc=(it.doc_comment or None),
            )
            facts.symbols.append(s)
            walk(it.children, s)

    walk(res.structure, None)

    for imp in res.imports or []:
        facts.imports.append(Imp(source=imp.source.strip()[:300], items=list(imp.items or []),
                                 line=imp.span.start_line + 1))

    q = _tags_query(language)
    if q is None:
        facts.notes.append(f"no tags query for {language}: call graph unavailable")
        return facts

    tree = tslp.get_parser(language).parse(source)
    cursor = ts.QueryCursor(q)
    tag_defs = []
    for _, caps in cursor.matches(tree.root_node):
        names = caps.get("name")
        if not names:
            continue
        name_node = names[0]
        name = name_node.text.decode("utf-8", errors="replace")
        for cap, nodes in caps.items():
            if cap == "reference.call":
                n = nodes[0]
                member = _is_member(n, name_node)
                facts.calls.append(Call(name=name, line=name_node.start_point[0] + 1, byte=n.start_byte,
                                        member=member, receiver=_receiver(n, name_node) if member else None))
            elif cap.startswith("definition.") and not facts.symbols:
                n = nodes[0]
                tag_defs.append(Sym(
                    name=name, qualname=name, kind=cap.split(".", 1)[1],
                    start_line=n.start_point[0] + 1, end_line=n.end_point[0] + 1,
                    start_byte=n.start_byte, end_byte=n.end_byte,
                    signature=n.text.decode("utf-8", errors="replace").split("\n", 1)[0][:200],
                ))
    if tag_defs:  # structure extraction unsupported for this language; fall back to tags
        facts.symbols = tag_defs
    return facts
