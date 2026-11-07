"""Code graph stored in LadybugDB (embedded Cypher graph database).

Schema
  (:File {path, language, lines, code_lines, parse_errors})
  (:Symbol {id, name, qualname, kind, path, start_line, end_line, signature})
  (:External {name})            unresolved / third-party call targets
  (:Module {name})              import sources
  (File)-[:DEFINES]->(Symbol)
  (Symbol)-[:CONTAINS]->(Symbol)
  (Symbol)-[:CALLS {line, confidence}]->(Symbol)
  (Symbol)-[:CALLS_EXTERNAL {line}]->(External)
  (File)-[:IMPORTS {line, items}]->(Module)

Module-level code is attributed to a pseudo symbol `<module>` per file.
Call resolution is name-based (no type inference); see `confidence`:
  same_file > imported > unique > ambiguous.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

import ladybug

from . import __version__
from .discover import resolve_root, source_files
from .parse import FileFacts, Sym, parse_file

INDEX_DIR = ".codebox"
DB_NAME = "graph.lbug"
META_NAME = "meta.json"
MAX_AMBIGUOUS = 3
JS_FAMILY = {"javascript", "typescript", "tsx", "jsx"}
# Language families whose imports _from_module reads. Others keep the looser rule: a name that appears in
# any import line resolves to every same-named candidate.
MODULE_AWARE = {"python", "js"}

SCHEMA = [
    "CREATE NODE TABLE File(path STRING PRIMARY KEY, language STRING, lines INT64, code_lines INT64, parse_errors INT64)",
    "CREATE NODE TABLE Symbol(id STRING PRIMARY KEY, name STRING, qualname STRING, kind STRING, path STRING, start_line INT64, end_line INT64, signature STRING)",
    "CREATE NODE TABLE External(name STRING PRIMARY KEY)",
    "CREATE NODE TABLE Module(name STRING PRIMARY KEY)",
    "CREATE REL TABLE DEFINES(FROM File TO Symbol)",
    "CREATE REL TABLE CONTAINS(FROM Symbol TO Symbol)",
    "CREATE REL TABLE CALLS(FROM Symbol TO Symbol, line INT64, confidence STRING)",
    "CREATE REL TABLE CALLS_EXTERNAL(FROM Symbol TO External, line INT64)",
    "CREATE REL TABLE IMPORTS(FROM File TO Module, line INT64, items STRING)",
]

WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|DELETE|DETACH|SET|MERGE|DROP|ALTER|COPY|INSTALL|LOAD|ATTACH|CHECKPOINT|REMOVE|IMPORT|EXPORT)\b",
    re.IGNORECASE,
)


def index_dir(root: Path) -> Path:
    return root / INDEX_DIR


def _sym_id(path: str, s: Sym | None) -> str:
    return f"{path}#<module>" if s is None else f"{path}#{s.qualname}@{s.start_line}"


def _family(language: str) -> str:
    """Languages that can call each other directly. A .tsx file never calls a Python function."""
    return "js" if language in JS_FAMILY else language


def _from_module(imp) -> list[str] | None:
    """Module path segments an import pulls names from: `from a.b import x` -> [a, b],
    `import {x} from '../api'` -> [api]. None when the import names no module we can read."""
    m = re.match(r"\s*from\s+([\w.]+)\s+import\b", imp.source)
    if m:
        return [s for s in m.group(1).split(".") if s]
    m = re.search(r"""\bfrom\s+['"]([^'"]+)['"]""", imp.source)
    if m:
        parts = [p for p in re.sub(r"\.[cm]?[jt]sx?$", "", m.group(1)).split("/") if p not in ("", ".", "..")]
        return parts[:-1] if parts and parts[-1] == "index" else parts
    return None


def _module_aliases(imports) -> dict[str, list[str]]:
    """Local name -> module path segments for imports that bind a module: `import util`,
    `from . import graph`, `from a.b import mod as x`, `import * as clock from './clock'`. Names that turn
    out to be classes or functions are harmless: they only matter when a project file has that module path."""
    out: dict[str, list[str]] = {}
    for imp in imports:
        base = _from_module(imp)
        is_plain = imp.source.lstrip().startswith("import ") and base is None
        for item in imp.items:
            if is_plain:
                out.setdefault(item.split(".")[0], [item.split(".")[0]])
            else:
                out.setdefault(item, (base or []) + [item])
        for target, alias in re.findall(r"([\w.*]+)\s+as\s+(\w+)", imp.source):
            if target == "*":
                if base:
                    out[alias] = base
            elif is_plain:
                out[alias] = target.split(".")
            else:
                out[alias] = (base or []) + target.split(".")
    return out


def _imported_from(imports) -> dict[str, list[str]]:
    """Name -> module path segments for names imported out of a module: `from a.b import x` maps x -> [a, b]."""
    out: dict[str, list[str]] = {}
    for imp in imports:
        base = _from_module(imp)
        if base:
            for item in imp.items:
                out.setdefault(item, base)
    return out


def _module_parts(path: str) -> list[str]:
    """A file's import path: directories plus stem, with package entry files (`__init__`, `index`) dropped."""
    p = Path(path)
    parts = list(p.parent.parts)
    return parts if p.stem in ("__init__", "index", "mod") else parts + [p.stem]


def _match_len(segs: list[str], parts: list[str]) -> int:
    """Longest tail of the import path `segs` found as a contiguous run in the file's `parts`.
    `engine.apps.cli.__main__` scores 4 against apps/cli/src/engine/apps/cli/__main__.py and 1 against
    apps/control_server/.../__main__.py. Matching anywhere, not only at the end, covers re-exports:
    `from engine.runtime import x` reaches engine/runtime/config.py."""
    for k in range(len(segs), 0, -1):
        tail = segs[-k:]
        for i in range(len(parts) - k + 1):
            if parts[i:i + k] == tail:
                return k
    return 0


def _narrow(cands: list[tuple[str, str]], segs: list[str]) -> list[tuple[str, str]]:
    """The candidates whose file best matches the import path; empty when none matches at all."""
    scored = [(_match_len(segs, _module_parts(p)), p, sid) for p, sid in cands]
    best = max((s for s, _, _ in scored), default=0)
    return [(p, sid) for s, p, sid in scored if s == best] if best else []


def _rows(res) -> list[dict]:
    cols = res.get_column_names()
    return [dict(zip(cols, r)) for r in res.get_all()]


# --------------------------------------------------------------------------- build

def build_index(root=None) -> dict:
    root = resolve_root(root)
    t0 = time.time()
    files = source_files(root)
    facts: list[FileFacts] = []
    notes: set[str] = set()
    for rel, lang in files:
        try:
            src = (root / rel).read_bytes()
        except OSError:
            continue
        f = parse_file(rel, lang, src)
        facts.append(f)
        notes.update(f.notes)

    # name -> [(path, sym_id, is_member_like)]. Member-like = callable as obj.name(): methods and members of
    # classes/structs, not functions nested inside other functions (those are only reachable by bare name).
    # Keyed by (language family, name) so a call only ever resolves within the language that can make it.
    by_name: dict[tuple[str, str], list[tuple[str, str, bool]]] = defaultdict(list)
    # (family, name) -> [(path, sym_id)] of top-level definitions, for mod.name() calls
    top_level: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for f in facts:
        fam = _family(f.language)
        kinds = {s.qualname: s.kind for s in f.symbols}
        for s in f.symbols:
            nested_in_function = kinds.get(s.parent) in ("function", "method")
            member = s.kind == "method" or (s.parent is not None and not nested_in_function)
            by_name[(fam, s.name)].append((f.path, _sym_id(f.path, s), member))
            if s.parent is None:
                top_level[(fam, s.name)].append((f.path, _sym_id(f.path, s)))

    file_rows, sym_rows, defines, contains, calls, ext_calls, imports = [], [], [], [], [], [], []
    externals, modules = set(), set()

    for f in facts:
        file_rows.append({"path": f.path, "language": f.language, "lines": f.lines,
                          "code_lines": f.code_lines, "parse_errors": f.errors})
        mod_id = _sym_id(f.path, None)
        sym_rows.append({"id": mod_id, "name": "<module>", "qualname": "<module>", "kind": "module",
                         "path": f.path, "start_line": 1, "end_line": max(f.lines, 1), "signature": ""})
        defines.append({"f": f.path, "s": mod_id})
        qual_to_id = {}
        for s in f.symbols:
            sid = _sym_id(f.path, s)
            qual_to_id[s.qualname] = sid
            sym_rows.append({"id": sid, "name": s.name, "qualname": s.qualname, "kind": s.kind,
                             "path": f.path, "start_line": s.start_line, "end_line": s.end_line,
                             "signature": s.signature})
            defines.append({"f": f.path, "s": sid})
        for s in f.symbols:
            if s.parent and s.parent in qual_to_id:
                contains.append({"a": qual_to_id[s.parent], "b": _sym_id(f.path, s)})

        imported_names = {i for imp in f.imports for i in imp.items} | {
            w for imp in f.imports for w in re.findall(r"\w+", imp.source)}
        for imp in f.imports:
            modules.add(imp.source)
            imports.append({"f": f.path, "m": imp.source, "line": imp.line, "items": ",".join(imp.items)})

        fam = _family(f.language)
        module_aware = fam in MODULE_AWARE
        aliases = _module_aliases(f.imports)
        origins = _imported_from(f.imports) if module_aware else {}
        seen = set()
        for c in f.calls:
            enc = f.enclosing(c.byte)
            src_id = _sym_id(f.path, enc)
            allc = by_name.get((fam, c.name), [])
            mod = aliases.get(c.receiver) if c.receiver else None
            mod_cands = _narrow(top_level.get((fam, c.name), []), mod) if mod else []
            if mod_cands:  # mod.name() where `mod` is an imported project module
                cands = mod_cands
            elif c.member:  # obj.name(): only methods / nested members are plausible targets
                cands = [(p, sid) for p, sid, m in allc if m]
            else:         # bare name(): prefer top-level definitions, fall back to anything
                cands = [(p, sid) for p, sid, m in allc if not m] or [(p, sid) for p, sid, _ in allc]
            same = [sid for p, sid in cands if p == f.path]
            origin = origins.get(c.name) if not c.member else None
            if mod_cands:
                targets = [sid for _, sid in cands]
                conf = "imported" if len(targets) == 1 else "ambiguous"
            elif same:
                targets, conf = same, "same_file"
            elif origin:  # `from mod import name`: only definitions in a file matching `mod` qualify
                targets = [sid for _, sid in _narrow(cands, origin)]
                conf = "imported" if len(targets) == 1 else "ambiguous" if targets else None
            elif cands and not module_aware and c.name in imported_names:
                targets, conf = [sid for _, sid in cands], "imported"
            elif len(cands) == 1:
                targets, conf = [cands[0][1]], "unique"
            elif 1 < len(cands) <= MAX_AMBIGUOUS:
                targets, conf = [sid for _, sid in cands], "ambiguous"
            else:
                targets, conf = [], None
            if targets:
                for t in targets[:MAX_AMBIGUOUS]:
                    key = (src_id, t, c.line)
                    if key not in seen:
                        seen.add(key)
                        calls.append({"a": src_id, "b": t, "line": c.line, "confidence": conf})
            else:
                externals.add(c.name)
                ext_calls.append({"a": src_id, "b": c.name, "line": c.line})

    # Rebuild from scratch: simple and correct. Incremental indexing is a TODO.
    idx = index_dir(root)
    idx.mkdir(exist_ok=True)
    (idx / ".gitignore").write_text("*\n")
    db_path = idx / DB_NAME
    for p in idx.glob(DB_NAME + "*"):
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    db = ladybug.Database(str(db_path))
    conn = ladybug.Connection(db)
    for stmt in SCHEMA:
        conn.execute(stmt)

    def load(q: str, rows: list[dict], chunk: int = 5000):
        for i in range(0, len(rows), chunk):
            conn.execute(q, {"rows": rows[i:i + chunk]})

    load("UNWIND $rows AS r CREATE (:File {path:r.path, language:r.language, lines:r.lines, code_lines:r.code_lines, parse_errors:r.parse_errors})", file_rows)
    load("UNWIND $rows AS r CREATE (:Symbol {id:r.id, name:r.name, qualname:r.qualname, kind:r.kind, path:r.path, start_line:r.start_line, end_line:r.end_line, signature:r.signature})", sym_rows)
    load("UNWIND $rows AS r CREATE (:External {name:r})", sorted(externals))
    load("UNWIND $rows AS r CREATE (:Module {name:r})", sorted(modules))
    load("UNWIND $rows AS r MATCH (f:File {path:r.f}), (s:Symbol {id:r.s}) CREATE (f)-[:DEFINES]->(s)", defines)
    load("UNWIND $rows AS r MATCH (a:Symbol {id:r.a}), (b:Symbol {id:r.b}) CREATE (a)-[:CONTAINS]->(b)", contains)
    load("UNWIND $rows AS r MATCH (a:Symbol {id:r.a}), (b:Symbol {id:r.b}) CREATE (a)-[:CALLS {line:r.line, confidence:r.confidence}]->(b)", calls)
    load("UNWIND $rows AS r MATCH (a:Symbol {id:r.a}), (b:External {name:r.b}) CREATE (a)-[:CALLS_EXTERNAL {line:r.line}]->(b)", ext_calls)
    load("UNWIND $rows AS r MATCH (f:File {path:r.f}), (m:Module {name:r.m}) CREATE (f)-[:IMPORTS {line:r.line, items:r.items}]->(m)", imports)
    conn.close()
    db.close()

    stats = {
        "root": str(root), "files": len(file_rows), "symbols": len(sym_rows) - len(file_rows),
        "calls_resolved": len(calls), "calls_external": len(ext_calls), "imports": len(imports),
        "languages": dict(sorted(_count(f.language for f in facts).items(), key=lambda x: -x[1])),
        "seconds": round(time.time() - t0, 2), "indexed_at": time.time(), "version": __version__,
    }
    (idx / META_NAME).write_text(json.dumps(stats, indent=2))
    stats["notes"] = sorted(notes)[:20]
    return stats


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return dict(d)


# --------------------------------------------------------------------------- read

def is_stale(root: Path) -> bool:
    meta = index_dir(root) / META_NAME
    if not meta.exists():
        return True
    info = json.loads(meta.read_text())
    if info.get("version") != __version__:  # built by another codebox: resolution rules may have changed
        return True
    built = info.get("indexed_at", 0)
    for rel, _ in source_files(root):
        try:
            if (root / rel).stat().st_mtime > built:
                return True
        except OSError:
            pass
    return False


def ensure_index(root, refresh: bool = False) -> Path:
    root = resolve_root(root)
    if refresh or is_stale(root):
        build_index(str(root))
    return root


def run_query(root, cypher: str, params: dict | None = None, limit: int = 200,
              allow_write: bool = False, auto_index: bool = True) -> list[dict]:
    root = ensure_index(root) if auto_index else resolve_root(root)
    if not allow_write and WRITE_KEYWORDS.search(cypher):
        raise ValueError("Only read-only Cypher is allowed (MATCH/RETURN/WITH/...)")
    db = ladybug.Database(str(index_dir(root) / DB_NAME), read_only=not allow_write)
    conn = ladybug.Connection(db)
    try:
        rows = _rows(conn.execute(cypher, params or {}))
    finally:
        conn.close()
        db.close()
    return rows[:limit]


SYM_MATCH = "(s.name = $q OR s.qualname = $q OR s.id = $q)"


def find_symbols(root, query: str, kind: str | None = None, limit: int = 50) -> list[dict]:
    q = (f"MATCH (s:Symbol) WHERE s.kind <> 'module' AND lower(s.qualname) CONTAINS lower($q) "
         + ("AND s.kind = $kind " if kind else "")
         + "RETURN s.qualname AS qualname, s.kind AS kind, s.path AS path, s.start_line AS line, "
           "s.end_line AS end_line, s.signature AS signature, s.id AS id ORDER BY size(s.qualname) LIMIT $limit")
    params = {"q": query, "limit": limit}
    if kind:
        params["kind"] = kind
    return run_query(root, q, params, limit)


def callers(root, symbol: str, depth: int = 1, limit: int = 100) -> list[dict]:
    depth = max(1, min(depth, 6))
    q = (f"MATCH p = (c:Symbol)-[:CALLS*1..{depth}]->(s:Symbol) WHERE {SYM_MATCH} "
         "WITH c, s, min(length(p)) AS d "
         "RETURN c.qualname AS caller, c.path AS path, c.start_line AS line, d AS depth, s.qualname AS target "
         "ORDER BY depth, path, line LIMIT $limit")
    rows = run_query(root, q, {"q": symbol, "limit": limit}, limit)
    # Annotate direct callers with the exact call-site lines (the evidence to cite).
    sites = run_query(root, f"MATCH (c:Symbol)-[r:CALLS]->(s:Symbol) WHERE {SYM_MATCH} "
                            "RETURN c.qualname AS caller, c.path AS path, collect(DISTINCT r.line) AS lines",
                      {"q": symbol}, 10_000, auto_index=False)
    by = {(x["caller"], x["path"]): sorted(x["lines"]) for x in sites}
    for r in rows:
        r["def_line"] = r["line"]
        lines = by.get((r["caller"], r["path"]))
        if r["depth"] == 1 and lines:
            r["line"], r["call_lines"] = lines[0], lines
    return rows


def callees(root, symbol: str, depth: int = 1, limit: int = 100) -> list[dict]:
    depth = max(1, min(depth, 6))
    q = (f"MATCH p = (s:Symbol)-[:CALLS*1..{depth}]->(c:Symbol) WHERE {SYM_MATCH} "
         "WITH c, min(length(p)) AS d "
         "RETURN c.qualname AS callee, c.path AS path, c.start_line AS line, d AS depth "
         "ORDER BY depth, path, line LIMIT $limit")
    rows = run_query(root, q, {"q": symbol, "limit": limit}, limit)
    ext = run_query(root, f"MATCH (s:Symbol)-[r:CALLS_EXTERNAL]->(e:External) WHERE {SYM_MATCH} "
                          "RETURN DISTINCT e.name AS external, s.path AS path, r.line AS line ORDER BY line LIMIT $limit",
                    {"q": symbol, "limit": limit}, limit, auto_index=False)
    return rows + [{**r, "depth": 1} for r in ext]


def impact(root, symbol: str, depth: int = 4) -> dict:
    """Blast radius: everything that transitively calls `symbol`, grouped by file."""
    rows = callers(root, symbol, depth=depth, limit=1000)
    by_file: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_file[r["path"]].append(f'{r["caller"]}:{r["line"]} (d{r["depth"]})')
    return {"symbol": symbol, "transitive_callers": len(rows), "files": len(by_file),
            "by_file": dict(sorted(by_file.items()))}


def overview(root, top: int = 15) -> dict:
    root = ensure_index(root)
    meta = json.loads((index_dir(root) / META_NAME).read_text())
    fan_in = run_query(root, "MATCH (c:Symbol)-[:CALLS]->(s:Symbol) RETURN s.qualname AS symbol, s.path AS path, "
                             "s.start_line AS line, count(DISTINCT c) AS callers ORDER BY callers DESC LIMIT $n",
                       {"n": top}, auto_index=False)
    fan_out = run_query(root, "MATCH (s:Symbol)-[:CALLS]->(c:Symbol) WHERE s.kind <> 'module' RETURN s.qualname AS symbol, "
                              "s.path AS path, s.start_line AS line, count(DISTINCT c) AS callees ORDER BY callees DESC LIMIT $n",
                        {"n": top}, auto_index=False)
    largest = run_query(root, "MATCH (f:File) RETURN f.path AS path, f.code_lines AS code_lines ORDER BY code_lines DESC LIMIT $n",
                        {"n": top}, auto_index=False)
    ext = run_query(root, "MATCH (:Symbol)-[:CALLS_EXTERNAL]->(e:External) RETURN e.name AS name, count(*) AS uses "
                          "ORDER BY uses DESC LIMIT $n", {"n": top}, auto_index=False)
    meta.pop("indexed_at", None)
    meta.pop("version", None)
    return {"stats": meta, "most_called": fan_in, "most_calling": fan_out,
            "largest_files": largest, "top_external_calls": ext}


def unreferenced(root, limit: int = 100) -> list[dict]:
    """Functions/methods with no inbound CALLS edge. Heuristic: misses dynamic dispatch, exports, tests, entrypoints."""
    q = ("MATCH (s:Symbol) WHERE s.kind IN ['function', 'method'] AND NOT EXISTS { MATCH (:Symbol)-[:CALLS]->(s) } "
         "RETURN s.qualname AS symbol, s.path AS path, s.start_line AS line ORDER BY path, line LIMIT $limit")
    return run_query(root, q, {"limit": limit}, limit)


def schema_text() -> str:
    return __doc__.split("Schema", 1)[1].strip()
