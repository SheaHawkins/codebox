"""Regenerate the README screenshots from a codebox HTML report.

Usage: uv run --with pillow python scripts/readme_screenshots.py <report.html> docs/images <scratch dir>
Isolates the panels named by their headings, renders each at 2x in headless Chrome (macOS path below), trims.
"""
import subprocess, sys, json
from pathlib import Path
from PIL import Image, ImageChops

REPORT = Path(sys.argv[1]); OUT = Path(sys.argv[2]); TMP = Path(sys.argv[3])
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SHOTS = [  # name, headings to keep (h2 keeps its whole section head; h3 keeps its panel), theme, graph view, width, height, extra JS
    ("overview", ["@header", "@tiles", "Recommendations"], "light", None, 1100, 1500, "document.querySelectorAll('ol.recs > li').forEach((li,i)=>{if(i>=4)li.remove()});"),
    ("call-graph", ["Call graph"], "light", "functions", 1100, 1000, ""),
    ("call-graph-files", ["Call graph"], "dark", "files", 1100, 1000, ""),
    ("dependency-layers", ["Dependency layers"], "light", None, 1100, 1400, ""),
    ("risk", ["Complexity distribution", "Size and complexity by file"], "light", None, 1100, 1100, ""),
]
ISOLATE = r"""
(function(){
  const want = %s, keep = new Set();
  const panelOf = h => h.tagName === 'H3' ? h.closest('.panel') : h.closest('.section-head') ? h.closest('section') : h;
  for (const w of want) {
    let el = null;
    if (w === '@header') el = document.querySelector('main > header');
    else if (w === '@tiles') el = document.querySelector('.tiles');
    else el = [...document.querySelectorAll('h2,h3')].find(h => h.textContent.trim() === w);
    if (!el) { document.title = 'MISSING ' + w; continue; }
    keep.add(w.startsWith('@') ? el : panelOf(el));
  }
  const needed = new Set();
  keep.forEach(k => { for (let e = k; e && e !== document.body; e = e.parentElement) needed.add(e); });
  const prune = root => [...root.children].forEach(c => {
    if (keep.has(c)) return;
    if (needed.has(c)) prune(c); else if (c.tagName !== 'SCRIPT' && c.tagName !== 'STYLE') c.style.display = 'none';
  });
  prune(document.body);
  document.querySelector('main').style.paddingBlock = '24px';
  %s
})();
"""
html = REPORT.read_text()
for name, want, theme, view, w, h, extra in SHOTS:
    pre = (f"<script>document.documentElement.dataset.theme='{theme}';"
           f"try{{localStorage.setItem('codebox-graph-view','{view or 'files'}')}}catch(e){{}}</script>")
    post = f"<script>window.addEventListener('load',()=>setTimeout(()=>{{{ISOLATE % (json.dumps(want), extra)}}},1500));</script>"
    page = TMP / f"shot-{name}.html"
    page.write_text(pre + html + post)
    raw = TMP / f"shot-{name}.png"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={w},{h}", "--virtual-time-budget=12000", f"--screenshot={raw}", page.as_uri()],
                   check=True, capture_output=True)
    img = Image.open(raw).convert("RGB")
    bg = Image.new("RGB", img.size, img.getpixel((2, 2)))
    box = ImageChops.difference(img, bg).getbbox()
    pad = 32
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    out = OUT / f"{name}.png"
    img.crop(box).save(out, optimize=True)
    print(name, img.crop(box).size, out.stat().st_size // 1024, "KB")
