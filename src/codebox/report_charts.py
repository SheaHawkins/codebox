"""SVG charts for the HTML report: dependency layers, size x complexity treemap, complexity distribution.

Every color comes from the page's CSS tokens (see report_html.CSS), so the charts follow light and dark mode.
"""

from __future__ import annotations

import html
from pathlib import Path

CHART_CSS = """
.deps .box rect { fill: var(--surface); stroke: var(--line); stroke-width: 1.5; }
.deps .box.entry rect { stroke: var(--accent); }
.deps .box text { fill: var(--fg); font-size: 11.5px; }
.deps .box:hover rect { stroke: var(--fg); }
.deps .edge path { fill: none; stroke: var(--muted); stroke-opacity: 0.55; }
.deps .edge.up path { stroke: var(--accent); stroke-opacity: 0.9; stroke-dasharray: 4 3; }
.deps .edge:hover path { stroke: var(--fg); stroke-opacity: 1; }
.deps .tip { fill: var(--muted); }
.deps .edge.up .tip { fill: var(--accent); }
.deps .layer-label { fill: var(--muted); font-size: 10px; letter-spacing: 0.06em; }
.tree rect { stroke: var(--surface); stroke-width: 2; }
.tree text { font-size: 11px; pointer-events: none; }
.tree text.on { fill: var(--on-accent); }
.tree text.off { fill: var(--fg); }
.tree g:hover rect { stroke: var(--fg); }
.ramp { height: 10px; border-radius: 3px; background: linear-gradient(to right,
  color-mix(in oklab, var(--accent) 15%, var(--surface)), var(--accent)); }
.ramp-labels { display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--muted); }
.hist .col { fill: color-mix(in oklab, var(--accent) 45%, var(--surface)); }
.hist .col.over { fill: var(--accent); }
.hist g:hover .col { stroke: var(--fg); stroke-width: 1.5; }
.hist text.v { fill: var(--fg); font-size: 12px; }
.key { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 0.78rem; color: var(--muted); }
.key span { display: inline-flex; align-items: center; gap: 6px; }
.key i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; }
.key i.light { background: color-mix(in oklab, var(--accent) 45%, var(--surface)); }
.key i.full { background: var(--accent); }
.key i.dash { width: 18px; height: 0; border-top: 2px dashed var(--accent); border-radius: 0; }
"""


def e(x) -> str:
    return html.escape(str(x), quote=True)


def _name(path: str, n: int = 18) -> str:
    name = Path(path).name
    return name if len(name) <= n else name[: n - 1] + "…"


# --------------------------------------------------------------------------- dependency layers

def _place_layers(layers: list[dict], deps: list[dict]) -> list[list[str]]:
    """Order files within each layer by the average position of the files they call (one barycenter pass,
    bottom-up), which keeps most edges short and uncrossed."""
    rows = [list(x["files"]) for x in reversed(layers)]  # bottom layer first
    out: dict[str, list[str]] = {}
    for d in deps:
        out.setdefault(d["path"], []).append(d["target"])
    pos: dict[str, float] = {f: i for i, f in enumerate(rows[0])} if rows else {}
    for row in rows[1:]:
        def key(f):
            xs = [pos[t] for t in out.get(f, []) if t in pos]
            return (sum(xs) / len(xs) if xs else len(row) / 2, f)
        row.sort(key=key)
        width = max(len(r) for r in rows)
        for i, f in enumerate(row):
            pos[f] = i * (width / max(len(row), 1))
    return list(reversed(rows))  # top layer first


def _edge(d: dict, at: dict, layer_of: dict, top: int, BW: float, BH: float, GY: float) -> str:
    """One caller -> callee curve with an arrowhead. Edges pointing up or sideways go against the layering."""
    a, b = d["path"], d["target"]
    (ax, ay), (bx, by) = at[a], at[b]
    up = layer_of[b] <= layer_of[a]
    x1, x2 = ax + BW / 2, bx + BW / 2
    sign = -1 if up else 1  # direction of travel along y
    y1, y2 = (ay, by + BH) if up else (ay + BH, by)
    c1, c2 = y1 + sign * GY * 0.6, y2 - sign * GY * 0.6
    base = y2 - sign * 8
    tip = f'<path class="tip" d="M{x2 - 4:.1f},{base:.1f} L{x2:.1f},{y2:.1f} L{x2 + 4:.1f},{base:.1f} Z"/>'
    return (f'<g class="edge{" up" if up else ""}"><title>{e(Path(a).name)} \u2192 {e(Path(b).name)}: '
            f'{e(d["calls"])} calls, first at {e(a)}:{e(d["line"])}</title>'
            f'<path d="M{x1:.1f},{y1:.1f} C{x1:.1f},{c1:.1f} {x2:.1f},{c2:.1f} {x2:.1f},{y2:.1f}" '
            f'stroke-width="{1 + 3 * int(d["calls"]) / top:.1f}"/>{tip}</g>')


def dependency_layers(layers: list[dict], deps: list[dict]) -> str:
    if not layers:
        return '<p class="empty">No cross-file calls.</p>'
    rows = _place_layers(layers, deps)
    BW, BH, GX, GY, PAD, LBL = 136, 30, 22, 58, 16, 54
    widest = max(len(r) for r in rows)
    W = LBL + PAD * 2 + widest * BW + (widest - 1) * GX
    H = PAD * 2 + len(rows) * BH + (len(rows) - 1) * GY
    at: dict[str, tuple[float, float]] = {}
    layer_of: dict[str, int] = {}
    for ri, row in enumerate(rows):
        rw = len(row) * BW + (len(row) - 1) * GX
        x0 = LBL + PAD + (widest * BW + (widest - 1) * GX - rw) / 2
        for i, f in enumerate(row):
            at[f] = (x0 + i * (BW + GX), PAD + ri * (BH + GY))
            layer_of[f] = ri
    top = max((int(d["calls"]) for d in deps if d["path"] in at and d["target"] in at), default=1)

    edges = [_edge(d, at, layer_of, top, BW, BH, GY) for d in sorted(deps, key=lambda d: int(d["calls"]))
             if d["path"] in at and d["target"] in at]

    boxes, labels = [], []
    for ri, row in enumerate(rows):
        n = len(rows) - 1 - ri
        labels.append(f'<text class="layer-label" x="{PAD}" y="{PAD + ri * (BH + GY) + BH / 2 + 4:.1f}">LAYER {n}</text>')
        for f in row:
            x, y = at[f]
            entry = ri == 0
            boxes.append(f'<g class="box{" entry" if entry else ""}"><title>{e(f)} · layer {n}</title>'
                         f'<rect x="{x:.1f}" y="{y:.1f}" width="{BW}" height="{BH}" rx="5"/>'
                         f'<text x="{x + BW / 2:.1f}" y="{y + BH / 2 + 4:.1f}" text-anchor="middle">{e(_name(f))}</text></g>')
    return (f'<div class="scroll"><svg class="deps" viewBox="0 0 {W:.0f} {H:.0f}" style="min-width:{min(W, 900) * 0.72:.0f}px;max-width:{W:.0f}px;margin:0 auto" '
            f'role="img" aria-label="Files arranged in dependency layers">'
            f'{"".join(labels)}{"".join(edges)}{"".join(boxes)}</svg></div>'
            '<div class="key"><span><i class="light"></i>arrow: calls into, thicker = more calls</span>'
            '<span><i class="dash"></i>points up: depends on a higher layer</span></div>')


# --------------------------------------------------------------------------- treemap

def _squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap (Bruls et al.). `values` sorted descending and scaled to sum to w*h."""
    rects, vals = [], list(values)
    while vals:
        short = min(w, h)

        def worst(row):
            s = sum(row)
            return max(max(short * short * r / (s * s), s * s / (short * short * r)) for r in row)

        i, row = 1, [vals[0]]
        while i < len(vals) and worst(row + [vals[i]]) <= worst(row):
            row.append(vals[i])
            i += 1
        s = sum(row)
        if w >= h:
            cw, yy = s / h, y
            for r in row:
                rects.append((x, yy, cw, r / cw))
                yy += r / cw
            x, w = x + cw, w - cw
        else:
            rh, xx = s / w, x
            for r in row:
                rects.append((xx, y, r / rh, rh))
                xx += r / rh
            y, h = y + rh, h - rh
        vals = vals[i:]
    return rects


def treemap(files: list[dict], max_files: int = 40) -> str:
    files = [f for f in files if f["code_lines"] > 0][:max_files]
    if not files:
        return '<p class="empty">No source files.</p>'
    W, H = 600, 320
    total = sum(f["code_lines"] for f in files)
    rects = _squarify([f["code_lines"] * W * H / total for f in files], 0, 0, W, H)
    dens = [f["ccn"] / f["code_lines"] * 100 for f in files]
    lo, hi = min(dens), max(dens)
    cells = []
    for f, d, (x, y, w, h) in zip(files, dens, rects):
        pct = 15 + (d - lo) / ((hi - lo) or 1) * 85
        cls = "on" if pct > 55 else "off"
        label = ""
        if w > 64 and h > 30:
            label = (f'<text class="{cls}" x="{x + 7:.1f}" y="{y + 16:.1f}">{e(_name(f["path"], int(w / 7.2)))}</text>'
                     f'<text class="{cls}" x="{x + 7:.1f}" y="{y + 30:.1f}" opacity="0.8">{e(f["code_lines"])} lines</text>')
        cells.append(f'<g><title>{e(f["path"])}: {e(f["code_lines"])} code lines, {e(f["functions"])} functions, '
                     f'CCN {e(f["ccn"])} ({d:.0f} per 100 lines)</title>'
                     f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" rx="3" '
                     f'style="fill:color-mix(in oklab, var(--accent) {pct:.0f}%, var(--surface))"/>{label}</g>')
    return (f'<svg class="tree" viewBox="0 0 {W} {H}" role="img" aria-label="Files sized by code lines, '
            f'shaded by complexity per line">{"".join(cells)}</svg>'
            f'<div><div class="ramp"></div><div class="ramp-labels"><span>{lo:.0f} branches per 100 lines</span>'
            f'<span>{hi:.0f}</span></div></div>')


# --------------------------------------------------------------------------- distribution

def histogram(dist: list[dict], threshold: int) -> str:
    if not dist or not any(d["functions"] for d in dist):
        return '<p class="empty">No functions measured.</p>'
    W, H, L, B, T = 600, 230, 36, 34, 20
    top = max(d["functions"] for d in dist)
    step = max(1, round(top / 4))
    ticks = list(range(0, top + step, step))
    ymax = ticks[-1]
    sy = lambda v: H - B - v / ymax * (H - B - T)  # noqa: E731
    slot = (W - L - 8) / len(dist)
    bw = slot * 0.62
    grid = "".join(f'<line x1="{L}" x2="{W - 8}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" class="g"/>'
                   f'<text x="{L - 8}" y="{sy(t) + 4:.1f}" text-anchor="end">{t}</text>' for t in ticks)
    cols = []
    for i, d in enumerate(dist):
        x = L + i * slot + (slot - bw) / 2
        y = sy(d["functions"])
        r = min(4.0, H - B - y)  # rounded top, never deeper than the bar itself
        over = d["min"] >= threshold
        col = "" if not d["functions"] else (
            f'<path class="col{" over" if over else ""}" d="M{x:.1f},{H - B} L{x:.1f},{y + r:.1f} '
            f'Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} L{x + bw - r:.1f},{y:.1f} Q{x + bw:.1f},{y:.1f} '
            f'{x + bw:.1f},{y + r:.1f} L{x + bw:.1f},{H - B} Z"/>')
        cols.append(f'<g><title>CCN {e(d["band"])}: {e(d["functions"])} functions</title>{col}'
                    f'<text class="v" x="{x + bw / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle">{e(d["functions"])}</text>'
                    f'<text x="{x + bw / 2:.1f}" y="{H - B + 18}" text-anchor="middle">{e(d["band"])}</text></g>')
    return (f'<svg class="hist" viewBox="0 0 {W} {H}" role="img" aria-label="Number of functions per complexity band">'
            f'<g class="grid">{grid}</g><line class="axis" x1="{L}" x2="{W - 8}" y1="{H - B}" y2="{H - B}"/>'
            f'{"".join(cols)}</svg>'
            f'<div class="key"><span><i class="light"></i>below CCN {threshold}</span>'
            f'<span><i class="full"></i>CCN {threshold} and up: split recommendations</span></div>')
