"""Reports: structure (coupling, cycles, hubs, misplaced code, crowded files), risk (complexity,
hotspots), dead code (verified candidates), and ranked recommendations. Markdown or JSON."""

from __future__ import annotations

import re
import time

from . import analyze, graph
from .discover import resolve_root

KINDS = ("all", "structure", "risk", "deadcode")
DEFAULT_EXCLUDE = ("tests/", "test/", "testdata/", "fixtures/")

# Thresholds for turning signals into recommendations.
COMPLEX_CCN = 15
HUB_MIN_FAN_IN = 3
HUB_MIN_FAN_OUT = 5
CROWDED_SYMBOLS = 40
CROWDED_LINES = 500
TOP_HOTSPOTS = 5

LIMITATIONS = [
    "Call edges are resolved by name; edges marked `ambiguous` are left out of the structure queries.",
    "Recommendations come from fixed thresholds on the metrics. Read the code before acting on them.",
    "Dead-code checks are textual: a name mentioned anywhere else counts as used, which can hide dead code "
    "that shares a name with something live.",
]


def _excluded(path: str, exclude: tuple[str, ...]) -> bool:
    return any(path.startswith(e) or f"/{e}" in f"/{path}" for e in exclude)


def _path_filter(var: str, exclude: tuple[str, ...]) -> str:
    return "".join(f" AND NOT {var}.path STARTS WITH '{e}' AND NOT {var}.path CONTAINS '/{e}'"
                   for e in exclude if re.fullmatch(r"[\w./-]+", e))


# --------------------------------------------------------------------------- structure

def structure(root, exclude=DEFAULT_EXCLUDE, top: int = 15) -> dict:
    fa, fb = _path_filter("a", exclude), _path_filter("b", exclude)
    q = graph.run_query
    coupling = q(root, f"""
        MATCH (a:Symbol)-[r:CALLS]->(b:Symbol)
        WHERE r.confidence <> 'ambiguous' AND a.path <> b.path {fa} {fb}
        WITH CASE WHEN a.path < b.path THEN a.path ELSE b.path END AS x,
             CASE WHEN a.path < b.path THEN b.path ELSE a.path END AS y,
             a.path AS from_path, min(r.line) AS first_line, count(*) AS n
        WITH x, y, sum(n) AS calls, collect({{from_path: from_path, line: first_line, n: n}}) AS dirs
        RETURN x, y, calls, dirs ORDER BY calls DESC LIMIT $n""", {"n": top}, auto_index=True)
    coupling_rows = []
    for c in coupling:
        d = max(c["dirs"], key=lambda d: d["n"])
        coupling_rows.append({"path": c["x"], "other": c["y"], "calls": c["calls"],
                              "directions": len(c["dirs"]),
                              "example": {"path": d["from_path"], "line": d["line"]}, "line": 1})

    cycles = q(root, f"""
        MATCH (a:Symbol)-[r1:CALLS]->(b:Symbol), (c:Symbol)-[r2:CALLS]->(d:Symbol)
        WHERE r1.confidence <> 'ambiguous' AND r2.confidence <> 'ambiguous'
          AND a.path = d.path AND b.path = c.path AND a.path < b.path {fa} {fb}
        RETURN a.path AS path, min(r1.line) AS line, b.path AS other, min(r2.line) AS other_line
        LIMIT $n""", {"n": top}, auto_index=False)

    hubs = q(root, f"""
        MATCH (c:Symbol)-[r1:CALLS]->(s:Symbol)-[r2:CALLS]->(d:Symbol)
        WHERE s.kind <> 'module' AND r1.confidence <> 'ambiguous' AND r2.confidence <> 'ambiguous'
              {_path_filter('s', exclude)}
        WITH s, count(DISTINCT c) AS fan_in, count(DISTINCT d) AS fan_out
        WHERE fan_in >= 2 AND fan_out >= 2
        RETURN s.qualname AS symbol, s.path AS path, s.start_line AS line, fan_in, fan_out
        ORDER BY fan_in * fan_out DESC LIMIT $n""", {"n": top}, auto_index=False)

    misplaced = q(root, f"""
        MATCH (c:Symbol)-[r:CALLS]->(s:Symbol)
        WHERE s.kind IN ['function','method'] AND r.confidence <> 'ambiguous' {_path_filter('s', exclude)}
        WITH s, count(*) AS calls, collect(DISTINCT c.path) AS from_files
        WHERE size(from_files) = 1 AND from_files[1] <> s.path AND calls >= 2
        RETURN s.qualname AS symbol, s.path AS path, s.start_line AS line,
               from_files[1] AS only_caller_file, calls ORDER BY calls DESC LIMIT $n""",
                  {"n": top}, auto_index=False)

    crowded = q(root, f"""
        MATCH (f:File)-[:DEFINES]->(s:Symbol)
        WHERE s.kind IN ['function','method','class'] {_path_filter('f', exclude)}
        RETURN f.path AS path, 1 AS line, f.code_lines AS code_lines, count(s) AS symbols
        ORDER BY symbols DESC LIMIT $n""", {"n": top}, auto_index=False)

    return {"coupling": coupling_rows, "cycles": cycles, "hubs": hubs,
            "misplaced": misplaced, "crowded_files": crowded}


# --------------------------------------------------------------------------- risk

def risk(root, exclude=DEFAULT_EXCLUDE, top: int = 15, days: int = 180) -> dict:
    cx = analyze.complexity(root, min_ccn=10, limit=10_000)
    funcs = [f for f in cx["functions"] if not _excluded(f["path"], exclude)][:top]
    hs = analyze.hotspots(root, days, limit=10_000)
    hot = [h for h in hs.get("hotspots", []) if not _excluded(h["path"], exclude)][:top]
    worst = {}
    for f in cx["functions"]:
        worst.setdefault(f["path"], f)
    for h in hot:
        w = worst.get(h["path"])
        h["line"] = w["line"] if w else 1
        if w:
            h["worst_function"] = f'{w["function"]} (CCN {w["ccn"]})'
    out = {"complex_functions": funcs, "hotspots": hot, "window_days": days}
    if hs.get("error"):
        out["hotspots_error"] = hs["error"]
    return out


# --------------------------------------------------------------------------- dead code

def _decorated(lines: list[str], line: int) -> bool:
    """True if the definition at 1-based `line` has a decorator directly above it."""
    return line >= 2 and lines[line - 2].strip().startswith("@")


def deadcode(root, exclude=DEFAULT_EXCLUDE, limit: int = 200) -> dict:
    """Unreferenced functions, sorted into likely dead / registered by a decorator / mentioned elsewhere."""
    rootp = resolve_root(root)
    cands = [c for c in graph.unreferenced(root, limit=10_000)
             if not _excluded(c["path"], exclude) and not c["symbol"].split(".")[-1].startswith("test_")]
    likely, decorated, mentioned = [], [], []
    texts: dict[str, list[str]] = {}
    for c in cands[:limit]:
        name = c["symbol"].split(".")[-1]
        lines = texts.setdefault(c["path"], (rootp / c["path"]).read_text(errors="replace").splitlines())
        if _decorated(lines, c["line"]):
            decorated.append(c)
            continue
        try:
            hits = analyze.grep(rf"\b{re.escape(name)}\b", root, limit=50)["matches"]
        except RuntimeError:  # ripgrep missing: report unchecked
            mentioned.append({**c, "mentions": [], "unchecked": True})
            continue
        others = [h for h in hits if not (h["path"] == c["path"] and h["line"] == c["line"])
                  and not h["path"].startswith(".codebox/")]
        if others:
            mentioned.append({**c, "mentions": [f'{h["path"]}:{h["line"]}' for h in others[:3]],
                              "mention_count": len(others)})
        else:
            likely.append(c)
    return {"likely_dead": likely, "decorated": decorated, "mentioned_elsewhere": mentioned,
            "candidates": len(cands)}


# --------------------------------------------------------------------------- recommendations
# Each rule turns one signal into recommendations. `weight` ranks them: bigger means more payoff.
# Hotspot files double the weight, since a fix there pays off on every future change.

def _rec(kind, path, line, what, evidence, gain, cost, weight) -> dict:
    return {"kind": kind, "path": path, "line": line, "what": what, "evidence": evidence,
            "gain": gain, "cost": cost, "weight": weight}


def _rec_complex(r: dict, hot: set[str]) -> list[dict]:
    out = []
    for f in r.get("complex_functions", []):
        if f["ccn"] < COMPLEX_CCN:
            continue
        is_hot = f["path"] in hot
        out.append(_rec("split_function", f["path"], f["line"], f'Split `{f["function"]}` into named steps',
                        f'CCN {f["ccn"]}, {f["nloc"]} lines' + (", in a top hotspot file" if is_hot else ""),
                        f'CCN {f["ccn"]} spread across smaller functions with one job each',
                        "internal to the function; callers unchanged if the signature stays",
                        f["ccn"] * (2 if is_hot else 1)))
    return out


def _rec_cycles(s: dict) -> list[dict]:
    return [_rec("break_cycle", c["path"], c["line"],
                 f'Break the dependency cycle between `{c["path"]}` and `{c["other"]}`',
                 f'{c["path"]}:{c["line"]} calls into {c["other"]}, and {c["other"]}:{c["other_line"]} calls back',
                 "the two files can change and be tested independently",
                 "move the shared piece to a third module, or invert one direction", 40)
            for c in s.get("cycles", [])]


def _rec_hubs(root, s: dict, hot: set[str]) -> list[dict]:
    out = []
    for h in s.get("hubs", []):
        if h["fan_in"] < HUB_MIN_FAN_IN or h["fan_out"] < HUB_MIN_FAN_OUT:
            continue
        rows = graph.run_query(root, "MATCH (c:Symbol)-[r:CALLS]->(s:Symbol {qualname:$q, path:$p}) "
                                     "WHERE r.confidence <> 'ambiguous' RETURN count(DISTINCT c.path) AS n",
                               {"q": h["symbol"], "p": h["path"]}, auto_index=False)
        files = rows[0]["n"] if rows else 0
        out.append(_rec("decouple_hub", h["path"], h["line"],
                        f'Separate the interface of `{h["symbol"]}` from the work behind it',
                        f'{h["fan_in"]} callers, {h["fan_out"]} callees',
                        "internal changes stop rippling out to callers",
                        f"{files} caller file(s) depend on it",
                        h["fan_in"] * h["fan_out"] // 2 * (2 if h["path"] in hot else 1)))
    return out


def _rec_misplaced(s: dict) -> list[dict]:
    return [_rec("move_function", m["path"], m["line"], f'Move `{m["symbol"]}` to `{m["only_caller_file"]}`',
                 f'all {m["calls"]} calls come from {m["only_caller_file"]}',
                 "one fewer cross-file dependency; the code sits next to its only user",
                 "one file's imports change", 10 + m["calls"])
            for m in s.get("misplaced", [])]


def _rec_crowded(s: dict, hot: set[str]) -> list[dict]:
    return [_rec("split_file", c["path"], 1,
                 f'Look for a split of `{c["path"]}` along groups of symbols with different users',
                 f'{c["symbols"]} symbols, {c["code_lines"]} code lines',
                 "smaller modules with one responsibility each",
                 "imports in every caller file change; run `impact` on the moved symbols",
                 c["symbols"] * (2 if c["path"] in hot else 1))
            for c in s.get("crowded_files", [])
            if c["symbols"] >= CROWDED_SYMBOLS or (c["code_lines"] or 0) >= CROWDED_LINES]


def _rec_dead(d: dict) -> list[dict]:
    return [_rec("delete_dead_code", x["path"], x["line"], f'Delete `{x["symbol"]}`',
                 "no call edge and no other mention of its name in the repo",
                 "less code to read and maintain", "none if it is truly unused; check for dynamic access first", 5)
            for x in d.get("likely_dead", [])]


def _merge(recs: list[dict]) -> list[dict]:
    """One entry per location: a function flagged by two rules gets one item with both pieces of evidence."""
    by_loc: dict[tuple, dict] = {}
    for x in sorted(recs, key=lambda x: -x["weight"]):
        key = (x["path"], x["line"])
        if key in by_loc:
            first = by_loc[key]
            first["evidence"] += f'; also {x["evidence"]}'
            first["also"] = first.get("also", []) + [x["what"]]
            first["weight"] += x["weight"] // 2
        else:
            by_loc[key] = dict(x)
    return sorted(by_loc.values(), key=lambda x: -x["weight"])


def recommendations(root, s: dict | None, r: dict | None, d: dict | None, top: int = 10) -> list[dict]:
    s, r, d = s or {}, r or {}, d or {}
    hot = {h["path"] for h in r.get("hotspots", [])[:TOP_HOTSPOTS]}
    recs = (_rec_complex(r, hot) + _rec_cycles(s) + _rec_hubs(root, s, hot) + _rec_misplaced(s)
            + _rec_crowded(s, hot) + _rec_dead(d))
    return _merge(recs)[:top]


# --------------------------------------------------------------------------- build

def build(root=None, kind: str = "all", exclude=DEFAULT_EXCLUDE, top: int = 15, days: int = 180) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown report {kind!r}; choose from {', '.join(KINDS)}")
    exclude = tuple(exclude)
    graph.ensure_index(root)
    stats = graph.overview(root, top=1)["stats"]
    rep = {"kind": kind, "root": str(resolve_root(root)), "generated": time.strftime("%Y-%m-%d %H:%M"),
           "stats": {k: stats.get(k) for k in ("files", "symbols", "calls_resolved", "languages")},
           "exclude": list(exclude)}
    if kind in ("all", "structure"):
        rep["structure"] = structure(root, exclude, top)
    if kind in ("all", "risk"):
        rep["risk"] = risk(root, exclude, top, days)
    if kind in ("all", "deadcode"):
        rep["deadcode"] = deadcode(root, exclude)
    if kind == "all":
        rep["recommendations"] = recommendations(root, rep["structure"], rep["risk"], rep["deadcode"])
    rep["limitations"] = LIMITATIONS
    return rep


# --------------------------------------------------------------------------- markdown

TITLES = {"all": "Codebase report", "structure": "Structure report", "risk": "Risk report",
          "deadcode": "Dead-code report"}


def _loc(row: dict) -> str:
    return f'`{row["path"]}:{row["line"]}`'


def _code(x) -> str:
    return f"`{x}`"


def _table(headers: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["_None found._", ""]
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return out + [""]


def _md_header(rep: dict) -> list[str]:
    st = rep["stats"]
    langs = ", ".join(f"{k} {v}" for k, v in (st.get("languages") or {}).items())
    excl = ", ".join(map(_code, rep["exclude"])) or "none"
    return [f'# {TITLES[rep["kind"]]}', "",
            f'`{rep["root"]}` · {rep["generated"]} · {st["files"]} files ({langs}), '
            f'{st["symbols"]} symbols, {st["calls_resolved"]} resolved calls', "",
            f"Excluded paths: {excl}.", ""]


def _md_recommendations(recs: list[dict]) -> list[str]:
    L = ["## Recommendations", "",
         "Ranked by expected payoff. Fixes in hotspot files count double; deletions come last.", ""]
    if not recs:
        return L + ["_Nothing crosses the thresholds._", ""]
    for i, x in enumerate(recs, 1):
        L += [f'{i}. **{x["what"]}** at {_loc(x)}', f'   - Evidence: {x["evidence"]}']
        L += [f"   - Also: {a}" for a in x.get("also", [])]
        L += [f'   - Gain: {x["gain"]}', f'   - Cost: {x["cost"]}']
    return L + [""]


def _md_structure(s: dict) -> list[str]:
    return (["## Structure", "", "### Coupling between files", ""]
            + _table(["Files", "Calls", "Directions", "Example call"],
                     [[f'`{c["path"]}` ↔ `{c["other"]}`', c["calls"], "both" if c["directions"] == 2 else "one",
                       _loc(c["example"])] for c in s["coupling"]])
            + ["### Dependency cycles", ""]
            + _table(["File A", "File B"],
                     [[_loc(c), f'`{c["other"]}:{c["other_line"]}`'] for c in s["cycles"]])
            + ["### Hub functions (many callers and many callees)", ""]
            + _table(["Function", "Location", "Fan-in", "Fan-out"],
                     [[_code(h["symbol"]), _loc(h), h["fan_in"], h["fan_out"]] for h in s["hubs"]])
            + ["### Functions called only from one other file", ""]
            + _table(["Function", "Location", "Only caller file", "Calls"],
                     [[_code(m["symbol"]), _loc(m), _code(m["only_caller_file"]), m["calls"]] for m in s["misplaced"]])
            + ["### Files by number of symbols", ""]
            + _table(["File", "Symbols", "Code lines"],
                     [[_code(c["path"]), c["symbols"], c["code_lines"]] for c in s["crowded_files"]]))


def _md_risk(r: dict) -> list[str]:
    L = (["## Risk", "", "### Most complex functions (CCN ≥ 10)", ""]
         + _table(["Function", "Location", "CCN", "Lines", "Params"],
                  [[_code(f["function"]), _loc(f), f["ccn"], f["nloc"], f["params"]] for f in r["complex_functions"]])
         + [f'### Hotspots (churn × complexity, last {r["window_days"]} days)', ""])
    if r.get("hotspots_error"):
        return L + [f'_{r["hotspots_error"]}._', ""]
    return L + _table(["File", "Commits", "Churn", "File CCN", "Worst function"],
                      [[_loc(h), h["commits"], h["churn"], h["ccn"], h.get("worst_function", "")]
                       for h in r["hotspots"]])


def _md_deadcode(d: dict) -> list[str]:
    def mentions(x):
        return ", ".join(map(_code, x.get("mentions", []))) or "unchecked"
    return (["## Dead code", "",
             f'{d["candidates"]} functions have no call edge. Each was checked for a decorator and for '
             "mentions of its name anywhere else in the repo.", "", "### Likely dead", ""]
            + _table(["Function", "Location"], [[_code(x["symbol"]), _loc(x)] for x in d["likely_dead"]])
            + ["### Registered by a decorator (probably in use)", ""]
            + _table(["Function", "Location"], [[_code(x["symbol"]), _loc(x)] for x in d["decorated"]])
            + ["### Name mentioned elsewhere (probably in use)", ""]
            + _table(["Function", "Location", "Mentioned at"],
                     [[_code(x["symbol"]), _loc(x), mentions(x)] for x in d["mentioned_elsewhere"]]))


def to_markdown(rep: dict) -> str:
    L = _md_header(rep)
    if "recommendations" in rep:
        L += _md_recommendations(rep["recommendations"])
    if "structure" in rep:
        L += _md_structure(rep["structure"])
    if "risk" in rep:
        L += _md_risk(rep["risk"])
    if "deadcode" in rep:
        L += _md_deadcode(rep["deadcode"])
    L += ["## Limitations", ""] + [f"- {x}" for x in rep["limitations"]] + [""]
    return "\n".join(L)
