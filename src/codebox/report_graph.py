"""Interactive force-directed call graph for the HTML report: drag nodes, zoom, hover to trace neighbours.

Uses d3 from cdnjs (allowed by the Artifact page contract). Without it the panel says so and the rest of the
report still renders. Colors come from the page's CSS tokens, so the graph follows light and dark mode.
"""

from __future__ import annotations

import json

D3 = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"

GRAPH_CSS = """
.fg-controls { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; font-size: 0.85rem; color: var(--muted); }
.fg-seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.fg-seg button, .fg-reset { font: inherit; color: var(--muted); background: var(--surface); border: 0; padding: 5px 12px; cursor: pointer; }
.fg-seg button + button { border-left: 1px solid var(--line); }
.fg-seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }
.fg-reset { border: 1px solid var(--line); border-radius: 6px; }
.fg-controls input[type=search] { font: inherit; color: var(--fg); background: var(--surface); border: 1px solid var(--line);
  border-radius: 6px; padding: 5px 9px; min-width: 0; width: 14rem; max-width: 100%; }
.fg-stage { position: relative; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); overflow: hidden; touch-action: none; }
.fg-stage svg { width: 100%; height: 560px; cursor: grab; }
.fg-stage svg:active { cursor: grabbing; }
.fg-stage .lnk { fill: none; stroke: var(--muted); stroke-opacity: 0.45; }
.fg-stage .lnk.cyc { stroke: var(--crit); stroke-opacity: 0.9; }
.fg-stage .lnk.out { stroke: var(--accent); stroke-opacity: 0.95; }
.fg-stage .lnk.in { stroke: var(--fg); stroke-opacity: 0.7; }
.fg-stage .lnk.dim { stroke-opacity: 0.05; }
.fg-stage .nd { stroke: var(--bg); stroke-width: 2; cursor: pointer; }
.fg-stage .nd.hit { stroke: var(--fg); stroke-width: 2.5; }
.fg-stage .dim { opacity: 0.12; }
.fg-stage text.nl { fill: var(--fg); font-size: 10.5px; paint-order: stroke; stroke: var(--bg); stroke-width: 3px; pointer-events: none; }
.fg-stage .ah { fill: var(--muted); }
.fg-stage .ah.cyc { fill: var(--crit); }
.fg-tip { position: fixed; pointer-events: none; background: var(--surface); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: 7px 9px; font-size: 0.78rem; line-height: 1.4; box-shadow: 0 4px 14px rgb(0 0 0 / 0.18);
  max-width: 340px; opacity: 0; z-index: 10; transition: opacity 80ms; }
.fg-tip b { display: block; font-family: var(--font-mono); font-weight: 600; overflow-wrap: anywhere; }
.fg-tip span { color: var(--muted); font-family: var(--font-mono); overflow-wrap: anywhere; }
.fg-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 0.78rem; color: var(--muted); }
.fg-legend span { display: inline-flex; align-items: center; gap: 6px; font-family: var(--font-mono); }
.fg-legend i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.fg-legend i.cyc { width: 16px; height: 0; border-top: 2px solid var(--crit); border-radius: 0; }
.fg-missing { margin: 0; padding: 24px; color: var(--muted); font-style: italic; }
@media (max-width: 520px) { .fg-stage svg { height: 440px; } }
"""

SCRIPT = r"""
(function () {
  const DATA = JSON.parse(document.getElementById('fg-data').textContent);
  const stage = document.getElementById('fg-stage');
  if (!window.d3) {
    stage.innerHTML = '<p class="fg-missing">The interactive graph needs d3 from cdnjs.cloudflare.com, which did not load. '
      + 'The dependency layers above show the same file edges.</p>';
    return;
  }
  const svgEl = stage.querySelector('svg');
  const tip = document.getElementById('fg-tip');
  const search = document.getElementById('fg-search');
  const legendEl = document.getElementById('fg-legend');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const base = p => p.split('/').pop();
  const dir = p => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '.';
  const COLORS = 7;
  let view = 'files', current = null, zoom = null;
  try { view = localStorage.getItem('codebox-graph-view') || view; } catch (e) {}

  function showTip(ev, h) {
    tip.innerHTML = h; tip.style.opacity = 1;
    tip.style.left = Math.max(8, Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8)) + 'px';
    tip.style.top = Math.max(8, Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 8)) + 'px';
  }
  const hideTip = () => { tip.style.opacity = 0; };

  // Group colors: the busiest groups get a palette slot, the rest share "other".
  function palette(nodes, key) {
    const n = d3.rollup(nodes, v => v.length, key);
    const top = [...n.keys()].sort((a, b) => n.get(b) - n.get(a) || (a < b ? -1 : 1)).slice(0, COLORS);
    const color = g => top.includes(g) ? `var(--c${top.indexOf(g) + 1})` : 'var(--muted)';
    legendEl.innerHTML = top.map(g => `<span><i style="background:${color(g)}"></i>${esc(g)}</span>`).join('')
      + (n.size > top.length ? '<span><i style="background:var(--muted)"></i>other</span>' : '')
      + '<span><i class="cyc"></i>calls both ways</span>';
    return color;
  }

  function filesData() {
    const size = new Map(DATA.files.map(f => [f.path, f.code_lines]));
    const agg = new Map();
    for (const c of DATA.calls) {
      const a = DATA.byId.get(c.source).path, b = DATA.byId.get(c.target).path;
      if (a === b) continue;
      const k = a + '\u0000' + b, cur = agg.get(k);
      if (cur) { cur.calls += c.calls; cur.line = Math.min(cur.line, c.line); }
      else agg.set(k, {source: a, target: b, calls: c.calls, line: c.line});
    }
    const links = [...agg.values()];
    const ids = new Set(links.flatMap(l => [l.source, l.target]));
    DATA.files.forEach(f => ids.add(f.path));
    const nodes = [...ids].map(p => ({id: p, label: base(p), group: dir(p), size: size.get(p) || 1, path: p, line: 1,
                                      detail: `${size.get(p) || 0} code lines`}));
    if (new Set(nodes.map(n => n.group)).size === 1) {
      // One directory: color each file as the Functions view does, so the two views read together.
      const perFile = d3.rollup(DATA.functions, v => v.length, f => f.path);
      nodes.forEach(n => { n.group = n.label; n.weight = perFile.get(n.path) || 0; });
      return {nodes, links, unit: 'calls', weighted: true};
    }
    return {nodes, links, unit: 'calls'};
  }

  function functionsData() {
    const nodes = DATA.functions.map(f => ({id: f.id, label: f.name.split('.').pop(), group: base(f.path), size: f.lines,
      path: f.path, line: f.line, name: f.name, detail: `${f.kind} · ${f.lines} lines`}));
    const links = DATA.calls.map(c => ({source: c.source, target: c.target, calls: c.calls, line: c.line}));
    return {nodes, links, unit: 'call sites'};
  }

  function draw() {
    const {nodes, links, unit, weighted} = view === 'files' ? filesData() : functionsData();
    const color = palette(weighted ? nodes.flatMap(n => Array(n.weight).fill(n)) : nodes, d => d.group);
    const svg = d3.select(svgEl); svg.selectAll('*').remove();
    const W = svgEl.clientWidth || 960, H = svgEl.clientHeight || 560;
    svg.attr('viewBox', [0, 0, W, H]);
    svg.append('defs').selectAll('marker').data(['e', 'cyc']).join('marker')
      .attr('id', d => 'fg-' + d).attr('viewBox', '0 -4 8 8').attr('refX', 8).attr('refY', 0)
      .attr('markerUnits', 'userSpaceOnUse').attr('markerWidth', 9).attr('markerHeight', 9).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L8,0L0,4').attr('class', d => d === 'cyc' ? 'ah cyc' : 'ah');
    const root = svg.append('g');
    // Pan by dragging the background; zoom with pinch or ctrl/cmd + wheel so the page still scrolls.
    zoom = d3.zoom().scaleExtent([0.2, 6])
      .filter(ev => ev.type === 'wheel' ? (ev.ctrlKey || ev.metaKey) : !ev.button)
      .on('zoom', ev => root.attr('transform', ev.transform));
    svg.call(zoom).on('dblclick.zoom', null);

    const pair = new Set(links.map(l => l.source + '\u0000' + l.target));
    links.forEach(l => { l.cycle = pair.has(l.target + '\u0000' + l.source); });
    const r = d3.scaleSqrt().domain([0, d3.max(nodes, d => d.size) || 1]).range(view === 'files' ? [6, 30] : [3.5, 16]);
    nodes.forEach(n => { n.r = r(n.size); });
    const w = d3.scaleSqrt().domain([1, d3.max(links, l => l.calls) || 1]).range([1, 4]);
    const degree = d3.rollup(links.flatMap(l => [l.source, l.target]), v => v.length, d => d);
    const labelled = n => view === 'files' || nodes.length <= 60 || (degree.get(n.id) || 0) >= 4;

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(view === 'files' ? 120 : 46).strength(0.3))
      .force('charge', d3.forceManyBody().strength(view === 'files' ? -520 : -120))
      .force('collide', d3.forceCollide(d => d.r + 4))
      .force('x', d3.forceX(W / 2).strength(view === 'files' ? 0.06 : 0.1)).force('y', d3.forceY(H / 2).strength(view === 'files' ? 0.09 : 0.14));

    const link = root.append('g').selectAll('path').data(links).join('path')
      .attr('class', d => d.cycle ? 'lnk cyc' : 'lnk').attr('stroke-width', d => w(d.calls))
      .attr('marker-end', d => `url(#fg-${d.cycle ? 'cyc' : 'e'})`);
    const node = root.append('g').selectAll('circle').data(nodes).join('circle')
      .attr('class', 'nd').attr('r', d => d.r).attr('fill', d => color(d.group))
      .call(d3.drag()
        .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); if (!ev.sourceEvent.shiftKey) { d.fx = null; d.fy = null; } }));
    const label = root.append('g').selectAll('text').data(nodes.filter(labelled)).join('text')
      .attr('class', 'nl').attr('text-anchor', 'middle').text(d => d.label);

    const near = new Map(nodes.map(n => [n.id, new Set([n.id])]));
    const callers = new Map(nodes.map(n => [n.id, 0])), callees = new Map(nodes.map(n => [n.id, 0]));
    links.forEach(l => { near.get(l.source.id).add(l.target.id); near.get(l.target.id).add(l.source.id);
                         callees.set(l.source.id, callees.get(l.source.id) + 1); callers.set(l.target.id, callers.get(l.target.id) + 1); });
    let pinned = null;
    function focus(id) {
      const set = id && near.get(id);
      node.classed('dim', d => !!set && !set.has(d.id)).classed('hit', d => d.id === id);
      label.classed('dim', d => !!set && !set.has(d.id));
      link.attr('class', d => {
        const cls = ['lnk'];
        if (d.cycle) cls.push('cyc');
        if (set) cls.push(d.source.id === id ? 'out' : d.target.id === id ? 'in' : 'dim');
        return cls.join(' ');
      });
    }
    current = {focus, nodes};
    const nodeTip = d => `<b>${esc(d.name || d.path)}</b><span>${esc(d.path)}:${d.line}</span><br>${esc(d.detail)}<br>`
      + `calls ${callees.get(d.id)} · called by ${callers.get(d.id)} ${view === 'files' ? 'files' : 'functions'}`;
    node.on('mousemove', (ev, d) => { if (!pinned) focus(d.id); showTip(ev, nodeTip(d)); })
        .on('mouseleave', () => { if (!pinned) focus(null); hideTip(); })
        .on('click', (ev, d) => { ev.stopPropagation(); pinned = pinned === d.id ? null : d.id; focus(pinned || d.id); });
    link.on('mousemove', (ev, d) => showTip(ev, `<b>${esc(d.source.label)} → ${esc(d.target.label)}</b>`
          + `<span>${esc(d.source.path)}:${d.line}</span><br>${d.calls} ${unit}${d.cycle ? ' · also called back' : ''}`))
        .on('mouseleave', hideTip);
    svg.on('click', () => { pinned = null; focus(null); });

    sim.on('tick', () => {
      nodes.forEach(d => { d.x = Math.max(d.r + 2, Math.min(W - d.r - 2, d.x));
                           d.y = Math.max(d.r + 16, Math.min(H - d.r - 2, d.y)); });
      link.attr('d', d => {
        const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len, bend = d.cycle ? 0.18 * len : 0;  // bow two-way edges apart
        const sx = d.source.x + ux * d.source.r, sy = d.source.y + uy * d.source.r;
        const tx = d.target.x - ux * (d.target.r + 2), ty = d.target.y - uy * (d.target.r + 2);
        return bend ? `M${sx},${sy}Q${(sx + tx) / 2 - uy * bend},${(sy + ty) / 2 + ux * bend} ${tx},${ty}` : `M${sx},${sy}L${tx},${ty}`;
      });
      node.attr('cx', d => d.x).attr('cy', d => d.y);
      label.attr('x', d => d.x).attr('y', d => d.y - d.r - 4);
    });
    if (search.value) find();
  }

  function find() {
    const q = search.value.trim().toLowerCase();
    const hit = q && current.nodes.find(n => (n.name || n.path).toLowerCase().includes(q));
    current.focus(hit ? hit.id : null);
  }

  DATA.byId = new Map(DATA.functions.map(f => [f.id, f]));
  const buttons = document.querySelectorAll('#fg-view button');
  function select(v) {
    view = v;
    buttons.forEach(b => b.setAttribute('aria-pressed', b.dataset.view === v));
    try { localStorage.setItem('codebox-graph-view', v); } catch (e) {}
    draw();
  }
  buttons.forEach(b => b.addEventListener('click', () => select(b.dataset.view)));
  document.getElementById('fg-reset').addEventListener('click', () => select(view));
  search.addEventListener('input', find);
  select(view === 'functions' ? 'functions' : 'files');
})();
"""


def panel(cg: dict | None) -> str:
    """The graph panel: controls, the stage, the data as JSON, and the script."""
    if not cg or not cg.get("calls"):
        return ""
    data = json.dumps(cg, separators=(",", ":")).replace("</", "<\\/")
    return ('<div class="panel"><h3>Call graph</h3><p>Drag a node to move it, drag the background to pan, and pinch or '
            "hold Ctrl/Cmd and scroll to zoom. Hover a node to trace its calls: blue edges go out to what it calls, "
            "dark edges come in from its callers. Click a node to keep it highlighted; shift-drag pins it in place.</p>"
            '<div class="fg-controls"><div class="fg-seg" id="fg-view" role="group" aria-label="Graph level">'
            '<button type="button" data-view="files" aria-pressed="true">Files</button>'
            '<button type="button" data-view="functions" aria-pressed="false">Functions</button></div>'
            '<input type="search" id="fg-search" placeholder="Find a file or function" aria-label="Find a node">'
            '<button type="button" class="fg-reset" id="fg-reset">Re-layout</button></div>'
            '<div class="fg-legend" id="fg-legend"></div>'
            '<div class="fg-stage" id="fg-stage"><svg role="img" aria-label="Force-directed call graph"></svg></div>'
            '<div class="fg-tip" id="fg-tip"></div>'
            f'<script type="application/json" id="fg-data">{data}</script>'
            f'<script src="{D3}"></script><script>{SCRIPT}</script></div>')
