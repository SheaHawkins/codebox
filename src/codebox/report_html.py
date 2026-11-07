"""Render a report (from report.build) as a self-contained HTML page.

The page follows the claude.ai Artifact page contract (no doctype/html/head/body; <title> first;
theme tokens for light and dark; only Google Fonts and d3 from cdnjs as external resources), so it can be published
as an Artifact as-is, and it also opens directly in a browser.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from . import report_charts as charts
from . import report_graph

KIND_LABELS = {
    "split_function": "Split function",
    "break_cycle": "Break cycle",
    "decouple_hub": "Decouple hub",
    "move_function": "Move function",
    "split_file": "Split file",
    "delete_dead_code": "Delete dead code",
}

CSS = """
/* Layout: one reading column; summary tiles, then ranked actions, then the evidence behind them. */
:root {
  --bg: #f5f6f3; --surface: #ffffff; --fg: #1a201d; --muted: #5b6660; --line: #dde2dd;
  --accent: #2c5a87; --on-accent: #ffffff; --chip: #e8ece8;
  --crit: #c2412d; --c1: #2c6fc0; --c2: #d9602b; --c3: #1a9a6c; --c4: #c98a00; --c5: #c95488; --c6: #6250d6; --c7: #0f8b8d;
  --font-display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --font-body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111513; --surface: #181d1a; --fg: #e4e9e5; --muted: #98a39d; --line: #2b322e;
    --accent: #80a9d6; --on-accent: #0d1420; --chip: #232a26; color-scheme: dark;
    --crit: #ec7a66; --c1: #5f9be6; --c2: #e8814f; --c3: #3cbf8e; --c4: #dcaa2e; --c5: #e07aa6; --c6: #9a8cf0; --c7: #3cb6b8;
  }
}
:root[data-theme="dark"] {
  --bg: #111513; --surface: #181d1a; --fg: #e4e9e5; --muted: #98a39d; --line: #2b322e;
  --accent: #80a9d6; --on-accent: #0d1420; --chip: #232a26; color-scheme: dark;
    --crit: #ec7a66; --c1: #5f9be6; --c2: #e8814f; --c3: #3cbf8e; --c4: #dcaa2e; --c5: #e07aa6; --c6: #9a8cf0; --c7: #3cb6b8;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--fg); font: 15px/1.55 var(--font-body); margin: 0; }
.page { max-width: 1040px; margin: 0 auto; padding-block: 40px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; grid-template-columns: minmax(0, 1fr); gap: 48px; }
h1, h2, h3 { font-family: var(--font-display); font-weight: 600; text-wrap: balance; margin: 0; line-height: 1.15; }
h1 { font-size: 2.25rem; }
h2 { font-size: 1.5rem; }
h3 { font-size: 1.05rem; }
code, .mono { font-family: var(--font-mono); font-size: 0.86em; }
.eyebrow { font: 600 0.75rem/1 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.muted { color: var(--muted); }
header { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }
header p { margin: 0; max-width: 70ch; overflow-wrap: anywhere; }
section { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; min-width: 0; }
.section-head { display: grid; gap: 6px; }
.section-head p { margin: 0; color: var(--muted); max-width: 70ch; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; display: grid; gap: 4px; }
.tile b { font: 600 1.75rem/1.1 var(--font-display); font-variant-numeric: tabular-nums; }
.tile span { font-size: 0.8rem; color: var(--muted); }
ol.recs { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; counter-reset: rec; }
.rec { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px;
  display: grid; grid-template-columns: 2.25rem 1fr; gap: 4px 12px; counter-increment: rec; }
.rec::before { content: counter(rec); grid-row: 1 / span 3; font: 600 1.4rem/1.2 var(--font-display); color: var(--accent); font-variant-numeric: tabular-nums; }
.rec-title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; min-width: 0; }
.rec-title strong { font-weight: 600; }
.chip { font: 600 0.7rem/1 var(--font-mono); letter-spacing: 0.04em; text-transform: uppercase; background: var(--chip); color: var(--muted); padding: 5px 7px; border-radius: 4px; }
.loc { font-family: var(--font-mono); font-size: 0.8rem; color: var(--muted); overflow-wrap: anywhere; }
dl.facts { margin: 6px 0 0; display: grid; grid-template-columns: max-content 1fr; gap: 3px 12px; font-size: 0.9rem; min-width: 0; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 24px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px; display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 14px; min-width: 0; }
.panel > p { margin: 0; font-size: 0.85rem; color: var(--muted); }
.bars { display: grid; gap: 6px; }
.bar-row { display: grid; grid-template-columns: minmax(0, 11rem) 1fr 3rem; gap: 10px; align-items: center; font-size: 0.85rem; border-radius: 4px; }
.bar-row:hover { background: var(--chip); }
.bar-label { font-family: var(--font-mono); font-size: 0.78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { position: relative; height: 14px; }
.bar-fill { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 0 4px 4px 0; min-width: 2px; }
.bar-mark { position: absolute; top: -3px; bottom: -3px; width: 0; border-left: 1.5px dashed var(--muted); }
.bar-value { font-family: var(--font-mono); font-size: 0.8rem; text-align: right; font-variant-numeric: tabular-nums; }
.legend-note { font-size: 0.78rem; color: var(--muted); display: flex; gap: 6px; align-items: center; }
.legend-note i { display: inline-block; width: 0; height: 12px; border-left: 1.5px dashed var(--muted); }
.scroll { overflow-x: auto; min-width: 0; }
svg { display: block; width: 100%; height: auto; }
svg text { fill: var(--muted); font-family: var(--font-mono); font-size: 11px; }
svg .grid line { stroke: var(--line); stroke-width: 1; }
svg .axis { stroke: var(--muted); stroke-width: 1; }
svg .zone { fill: var(--accent); fill-opacity: 0.08; }
svg .pt { fill: var(--accent); stroke: var(--surface); stroke-width: 2; }
svg .pt.dim { fill: var(--muted); fill-opacity: 0.55; }
svg g.p:hover .pt { stroke: var(--fg); }
svg .lbl { fill: var(--fg); }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font: 600 0.72rem/1.2 var(--font-mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
table.matrix { width: auto; }
table.matrix th, table.matrix td { border: 0; padding: 0; }
table.matrix th.row { text-align: right; padding-right: 10px; font-weight: 400; text-transform: none; letter-spacing: 0; white-space: nowrap; }
table.matrix th.col { height: 9rem; vertical-align: bottom; padding-bottom: 6px; font-weight: 400; text-transform: none; letter-spacing: 0; }
table.matrix th.col span { writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; }
table.matrix td.cell { width: 2.4rem; height: 2.4rem; text-align: center; font: 0.78rem var(--font-mono); border: 2px solid var(--surface); border-radius: 4px; }
table.matrix td.self { background: var(--chip); }
.empty { margin: 0; color: var(--muted); font-style: italic; }
.dead-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap: 16px; }
.dead-groups ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; font-size: 0.85rem; }
.dead-groups li { display: grid; gap: 1px; }
footer ul { margin: 0; padding-left: 1.2em; color: var(--muted); font-size: 0.85rem; display: grid; gap: 4px; }
a:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 520px) {
  h1 { font-size: 1.75rem; }
  .bar-row { grid-template-columns: minmax(0, 7.5rem) 1fr 2.5rem; }
  .rec { grid-template-columns: 1.6rem 1fr; padding: 14px; }
}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
         '&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap">')


def e(x) -> str:
    return html.escape(str(x), quote=True)


def inline(text: str) -> str:
    """Escape, then turn `code` spans into <code>."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text))


def _short_paths(paths: list[str]) -> dict[str, str]:
    """Drop the directory prefix all paths share, so labels stay short."""
    if not paths:
        return {}
    parts = [p.split("/") for p in paths]
    common = 0
    while all(len(x) > common + 1 for x in parts) and len({x[common] for x in parts}) == 1:
        common += 1
    return {p: "/".join(x[common:]) for p, x in zip(paths, parts)}


# --------------------------------------------------------------------------- pieces

def _header(rep: dict) -> str:
    st = rep["stats"]
    name = Path(rep["root"]).name or rep["root"]
    langs = ", ".join(f"{k} {v}" for k, v in (st.get("languages") or {}).items())
    excl = ", ".join(f"<code>{e(x)}</code>" for x in rep["exclude"]) or "none"
    return (f'<header><span class="eyebrow">codebox report · {e(rep["kind"])}</span>'
            f"<h1>{e(name)}</h1>"
            f'<p class="muted"><span class="mono">{e(rep["root"])}</span> · generated {e(rep["generated"])} · '
            f"{e(st['files'])} files ({e(langs)})</p>"
            f'<p class="muted">Excluded from analysis: {excl}.</p></header>')


def _tiles(rep: dict) -> str:
    st = rep["stats"]
    t = [("Files", st["files"]), ("Symbols", st["symbols"]), ("Resolved calls", st["calls_resolved"])]
    if "recommendations" in rep:
        t.append(("Recommendations", len(rep["recommendations"])))
    if "risk" in rep:
        t.append(("Functions CCN ≥ 10", len(rep["risk"]["complex_functions"])))
    if "structure" in rep:
        t.append(("Dependency cycles", len(rep["structure"]["cycles"])))
    if "deadcode" in rep:
        t.append(("Likely dead", len(rep["deadcode"]["likely_dead"])))
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><b>{e(v)}</b><span>{e(k)}</span></div>' for k, v in t) + "</div>"


def _recommendations(recs: list[dict]) -> str:
    head = ('<div class="section-head"><h2>Recommendations</h2><p>Ranked by expected payoff. Fixes in hotspot '
            "files count double. These come from fixed thresholds, so read the cited code before acting.</p></div>")
    if not recs:
        return f'<section>{head}<p class="empty">Nothing crosses the thresholds.</p></section>'
    items = []
    for x in recs:
        also = "".join(f"<dt>Also</dt><dd>{inline(a)}</dd>" for a in x.get("also", []))
        items.append(
            f'<li class="rec"><div class="rec-title"><strong>{inline(x["what"])}</strong>'
            f'<span class="chip">{e(KIND_LABELS.get(x["kind"], x["kind"]))}</span></div>'
            f'<div class="loc">{e(x["path"])}:{e(x["line"])}</div>'
            f'<dl class="facts"><dt>Evidence</dt><dd>{inline(x["evidence"])}</dd>{also}'
            f'<dt>Gain</dt><dd>{inline(x["gain"])}</dd><dt>Cost</dt><dd>{inline(x["cost"])}</dd></dl></li>')
    return f'<section>{head}<ol class="recs">{"".join(items)}</ol></section>'


def _bars(rows: list[tuple[str, float, str]], mark: float | None = None, mark_label: str = "") -> str:
    """Horizontal bars: (label, value, hover text). One hue; an optional dashed threshold mark."""
    if not rows:
        return '<p class="empty">None found.</p>'
    top = max(max(v for _, v, _ in rows), mark or 0) or 1
    out = []
    for label, v, tip in rows:
        m = f'<span class="bar-mark" style="left:{mark / top * 100:.1f}%"></span>' if mark else ""
        out.append(f'<div class="bar-row" title="{e(tip)}"><span class="bar-label">{e(label)}</span>'
                   f'<span class="bar-track"><span class="bar-fill" style="width:{v / top * 100:.1f}%"></span>{m}</span>'
                   f'<span class="bar-value">{e(int(v) if float(v).is_integer() else v)}</span></div>')
    note = f'<div class="legend-note"><i></i>{e(mark_label)}</div>' if mark else ""
    return f'<div class="bars">{"".join(out)}</div>{note}'


def _risk(r: dict, threshold: int) -> str:
    funcs = r["complex_functions"]
    cx = _bars([(f["function"], f["ccn"], f'{f["path"]}:{f["line"]} · CCN {f["ccn"]} · {f["nloc"]} lines')
                for f in funcs], threshold, f"CCN {threshold}: threshold for a split recommendation")
    if r.get("hotspots_error"):
        hs = f'<p class="empty">{e(r["hotspots_error"])}.</p>'
    else:
        hs = _bars([(h["path"].split("/")[-1], h["score"],
                     f'{h["path"]} · {h["commits"]} commits × file CCN {h["ccn"]}'
                     + (f' · worst: {h["worst_function"]}' if h.get("worst_function") else ""))
                    for h in r["hotspots"]])
    return ('<section><div class="section-head"><h2>Risk</h2><p>Where complexity sits, and where it meets frequent '
            "change. Hover a bar for its location.</p></div>"
            '<div class="grid-2">'
            f'<div class="panel"><h3>Most complex functions</h3><p>Cyclomatic complexity (CCN) per function, '
            f"10 and above.</p>{cx}</div>"
            f'<div class="panel"><h3>Hotspots</h3><p>Commits in the last {e(r["window_days"])} days × file CCN. '
            f"High scores are complex files that keep changing.</p>{hs}</div></div>"
            '<div class="grid-2">'
            f'<div class="panel"><h3>Complexity distribution</h3><p>All {e(r.get("functions_total", 0))} functions by '
            f"CCN band. A healthy codebase is mostly low bands; a tail on the right is where splitting pays off."
            f'</p>{charts.histogram(r.get("distribution", []), threshold)}</div>'
            f'<div class="panel"><h3>Size and complexity by file</h3><p>Each box is a file, sized by code lines and '
            f"shaded by branches per 100 lines. Large, dark boxes hold the most logic to untangle.</p>"
            f'{charts.treemap(r.get("files", []))}</div></div></section>')


def _ticks(mx: int) -> list[int]:
    step = max(1, round(mx / 6))
    return list(range(0, mx + 1, step))


def _scatter_frame(W, H, L, R, T, B, xmax, ymax, sx, sy, min_in, min_out) -> str:
    """Grid, shaded hub zone, axes and tick labels for the fan-in/fan-out scatter."""
    g = ['<g class="grid">']
    g += [f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="{T}" y2="{H - B}"/>' for v in _ticks(xmax)]
    g += [f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>' for v in _ticks(ymax)]
    g.append("</g>")
    zone = (f'<rect class="zone" x="{sx(min_in):.1f}" y="{T}" width="{W - R - sx(min_in):.1f}" '
            f'height="{sy(min_out) - T:.1f}"/>'
            f'<text x="{W - R - 4}" y="{T + 14}" text-anchor="end">hub zone</text>')
    axes = (f'<line class="axis" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>'
            f'<line class="axis" x1="{L}" x2="{L}" y1="{T}" y2="{H - B}"/>')
    mid = (T + H - B) / 2
    labels = "".join(f'<text x="{sx(v):.1f}" y="{H - B + 16}" text-anchor="middle">{v}</text>' for v in _ticks(xmax))
    labels += "".join(f'<text x="{L - 8}" y="{sy(v) + 4:.1f}" text-anchor="end">{v}</text>' for v in _ticks(ymax))
    labels += (f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" text-anchor="middle">callers (fan-in)</text>'
               f'<text x="12" y="{mid:.0f}" text-anchor="middle" transform="rotate(-90 12 {mid:.0f})">callees (fan-out)</text>')
    return "".join(g) + zone + axes + labels


def _scatter(hubs: list[dict], min_in: int, min_out: int) -> str:
    """Fan-in (x) vs fan-out (y) per function; the shaded corner is the hub zone used by the recommendations."""
    if not hubs:
        return '<p class="empty">No functions with at least 2 callers and 2 callees.</p>'
    W, H, L, R, T, B = 600, 340, 46, 24, 16, 42
    xmax = max(max(h["fan_in"] for h in hubs), min_in) + 1
    ymax = max(max(h["fan_out"] for h in hubs), min_out) + 1
    sx = lambda v: L + v / xmax * (W - L - R)  # noqa: E731
    sy = lambda v: H - B - v / ymax * (H - T - B)  # noqa: E731

    frame = _scatter_frame(W, H, L, R, T, B, xmax, ymax, sx, sy, min_in, min_out)
    pts, used = [], []
    for h in sorted(hubs, key=lambda h: h["fan_in"] * h["fan_out"]):
        x, y = sx(h["fan_in"]), sy(h["fan_out"])
        hub = h["fan_in"] >= min_in and h["fan_out"] >= min_out
        lbl = ""
        if hub and all(abs(x - ux) > 60 or abs(y - uy) > 14 for ux, uy in used):
            used.append((x, y))
            anchor, dx = ("end", -9) if x > W * 0.7 else ("start", 9)
            lbl = f'<text class="lbl" x="{x + dx:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}">{e(h["symbol"])}</text>'
        pts.append(f'<g class="p"><title>{e(h["symbol"])} · {e(h["path"])}:{e(h["line"])} · '
                   f'{h["fan_in"]} callers, {h["fan_out"]} callees</title>'
                   f'<circle class="pt{"" if hub else " dim"}" cx="{x:.1f}" cy="{y:.1f}" r="5.5"/>{lbl}</g>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Fan-in versus fan-out per function">'
            f'{frame}{"".join(pts)}</svg>')


def _matrix(coupling: list[dict]) -> str:
    if not coupling:
        return '<p class="empty">No cross-file calls.</p>'
    files = sorted({c["path"] for c in coupling} | {c["other"] for c in coupling})
    short = _short_paths(files)
    n = {(c["path"], c["other"]): int(c["calls"]) for c in coupling}
    top = max(n.values())
    head = '<tr><th></th>' + "".join(f'<th class="col"><span class="mono">{e(short[f])}</span></th>' for f in files) + "</tr>"
    rows = []
    for a in files:
        cells = []
        for b in files:
            if a == b:
                cells.append('<td class="cell self"></td>')
                continue
            v = n.get((a, b)) or n.get((b, a)) or 0
            if not v:
                cells.append('<td class="cell"></td>')
                continue
            pct = 18 + v / top * 82
            fg = "var(--on-accent)" if pct > 55 else "var(--fg)"
            cells.append(f'<td class="cell" title="{e(short[a])} ↔ {e(short[b])}: {v} calls" '
                         f'style="background:color-mix(in oklab, var(--accent) {pct:.0f}%, var(--surface));color:{fg}">{v}</td>')
        rows.append(f'<tr><th class="row"><span class="mono">{e(short[a])}</span></th>{"".join(cells)}</tr>')
    return f'<div class="scroll"><table class="matrix">{head}{"".join(rows)}</table></div>'


def _table(headers: list[tuple[str, bool]], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="empty">None found.</p>'
    th = "".join(f'<th class="{"num" if num else ""}">{e(h)}</th>' for h, num in headers)
    body = "".join("<tr>" + "".join(f'<td class="{"num" if headers[i][1] else ""}">{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _loc(x: dict) -> str:
    return f'<span class="loc">{e(x["path"])}:{e(x["line"])}</span>'


def _structure(s: dict, min_in: int, min_out: int) -> str:
    cycles = _table([("File A", False), ("File B", False)],
                    [[f'<span class="loc">{e(c["path"])}:{e(c["line"])}</span>',
                      f'<span class="loc">{e(c["other"])}:{e(c["other_line"])}</span>'] for c in s["cycles"]])
    misplaced = _table([("Function", False), ("Defined at", False), ("Only caller file", False), ("Calls", True)],
                       [[f'<code>{e(m["symbol"])}</code>', _loc(m), f'<code>{e(m["only_caller_file"])}</code>',
                         e(m["calls"])] for m in s["misplaced"]])
    crowded = _table([("File", False), ("Symbols", True), ("Code lines", True)],
                     [[f'<code>{e(c["path"])}</code>', e(c["symbols"]), e(c["code_lines"])] for c in s["crowded_files"]])
    return ('<section><div class="section-head"><h2>Structure</h2><p>How the files depend on each other. '
            "Calls the resolver could only guess at are left out.</p></div>"
            f'<div class="panel"><h3>Dependency layers</h3><p>Files arranged by what they call. Entry points sit at the '
            f"top and foundations at the bottom; each arrow points from caller to callee. In a clean layering every "
            f"arrow points down.</p>{charts.dependency_layers(s.get('layers', []), s.get('file_deps', []))}</div>"
            f"{report_graph.panel(s.get('call_graph'))}"
            '<div class="grid-2">'
            f'<div class="panel"><h3>Coupling between files</h3><p>Calls between each pair of files, both directions '
            f"combined. Darker cells are more tightly coupled.</p>{_matrix(s['coupling'])}</div>"
            f'<div class="panel"><h3>Hub functions</h3><p>Callers against callees per function. In the shaded zone '
            f"(at least {min_in} callers and {min_out} callees), a change ripples both ways. Hover a point for "
            f"its location.</p>{_scatter(s['hubs'], min_in, min_out)}</div></div>"
            '<div class="grid-2">'
            f'<div class="panel"><h3>Dependency cycles</h3>{cycles}</div>'
            f'<div class="panel"><h3>Functions called only from one other file</h3>{misplaced}</div></div>'
            f'<div class="panel"><h3>Files by number of symbols</h3>{crowded}</div></section>')


def _deadcode(d: dict) -> str:
    def group(title, rows, note, extra=None):
        if not rows:
            body = '<p class="empty">None.</p>'
        else:
            body = "<ul>" + "".join(
                f'<li><code>{e(x["symbol"])}</code>{_loc(x)}{extra(x) if extra else ""}</li>' for x in rows) + "</ul>"
        return f'<div class="panel"><h3>{e(title)} · {len(rows)}</h3><p>{e(note)}</p>{body}</div>'

    def mentions(x):
        m = x.get("mentions") or []
        return f'<span class="muted" style="font-size:0.8rem">mentioned at {e(", ".join(m))}</span>' if m else ""

    return ('<section><div class="section-head"><h2>Dead code</h2>'
            f'<p>{e(d["candidates"])} functions have no call edge. Each was checked for a decorator and for '
            "mentions of its name elsewhere in the repo.</p></div>"
            '<div class="dead-groups">'
            + group("Likely dead", d["likely_dead"], "No call edge, no decorator, no other mention. Delete after a final check.")
            + group("Registered by a decorator", d["decorated"], "Probably called by a framework: handlers, commands, tools.")
            + group("Mentioned elsewhere", d["mentioned_elsewhere"], "Probably in use: stored, passed or referenced by name.",
                    mentions)
            + "</div></section>")


def to_html(rep: dict, complex_ccn: int = 15, hub_in: int = 3, hub_out: int = 5) -> str:
    name = Path(rep["root"]).name or "codebase"
    parts = [f"<title>{e(name)} code report</title>",
             '<meta name="claude:viewport-fit-sides" content="cover">',
             FONTS, f"<style>{CSS}{charts.CHART_CSS}{report_graph.GRAPH_CSS}</style>", '<main class="page">', _header(rep), _tiles(rep)]
    if "recommendations" in rep:
        parts.append(_recommendations(rep["recommendations"]))
    if "risk" in rep:
        parts.append(_risk(rep["risk"], complex_ccn))
    if "structure" in rep:
        parts.append(_structure(rep["structure"], hub_in, hub_out))
    if "deadcode" in rep:
        parts.append(_deadcode(rep["deadcode"]))
    parts.append('<footer><section><div class="section-head"><h2>Limitations</h2></div><ul>'
                 + "".join(f"<li>{inline(x)}</li>" for x in rep["limitations"]) + "</ul></section></footer>")
    parts.append("</main>")
    # ASCII-only output: non-ASCII becomes entities, so the page reads right even without a charset declaration
    return ("\n".join(parts) + "\n").encode("ascii", "xmlcharrefreplace").decode("ascii")
