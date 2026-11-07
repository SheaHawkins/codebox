import shutil
from pathlib import Path

import pytest

from codebox import analyze, graph

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    stats = graph.build_index(str(root))
    assert stats["files"] == 3
    return str(root)


def names(rows, key):
    return {r[key] for r in rows if key in r}


def test_outline(repo):
    o = analyze.outline("app.py", repo)
    quals = {s["qualname"] for s in o["symbols"]}
    assert {"Service", "Service.run", "Service.step", "main"} <= quals
    assert o["imports"][0]["source"].startswith("from util")


def test_cross_file_callers(repo):
    rows = graph.callers(repo, "helper")
    assert names(rows, "caller") == {"Service.run"}
    assert rows[0]["path"] == "app.py" and rows[0]["line"] == 7


def test_method_calls_and_depth(repo):
    assert "Service.step" in names(graph.callees(repo, "Service.run"), "callee")
    assert "main" in names(graph.callers(repo, "helper", depth=3), "caller")


def test_member_call_not_linked_to_function(repo):
    # Date.now() must not resolve to the top-level `now` function; bare now() must.
    rows = graph.callers(repo, "now")
    assert names(rows, "caller") == {"stamp"}
    assert rows[0]["call_lines"] == [6]


def test_module_qualified_call(repo):
    # util.twice() via `import util` links to util.py's top-level twice, not to a method.
    rows = graph.callers(repo, "twice")
    assert names(rows, "caller") == {"main"}
    assert rows[0]["path"] == "app.py" and rows[0]["call_lines"] == [16]
    conf = graph.run_query(repo, "MATCH (:Symbol)-[r:CALLS]->(:Symbol {name:'twice'}) RETURN r.confidence AS c")
    assert [r["c"] for r in conf] == ["imported"]
    assert "twice" not in names(graph.unreferenced(repo), "symbol")


def test_impact_and_unreferenced(repo):
    assert graph.impact(repo, "helper")["files"] == 1
    assert "unused" in names(graph.unreferenced(repo), "symbol")


def test_query_is_read_only(repo):
    assert graph.run_query(repo, "MATCH (f:File) RETURN count(f) AS n")[0]["n"] == 3
    with pytest.raises(ValueError):
        graph.run_query(repo, "MATCH (n) DETACH DELETE n")


def test_structural_search(repo):
    r = analyze.structural_search(None, "python", repo,
                                  rule={"kind": "except_clause", "has": {"kind": "pass_statement", "stopBy": "end"}})
    assert [(m["path"], m["line"]) for m in r["matches"]] == [("util.py", 4)]
    with pytest.raises(ValueError):
        analyze.structural_search("except $E: $$$B", "python", repo)


def test_complexity(repo):
    r = analyze.complexity(repo, min_ccn=2)
    assert any(f["function"].endswith("step") for f in r["functions"])


def test_report_all(repo):
    from codebox import report
    rep = report.build(repo)
    assert {"structure", "risk", "deadcode", "recommendations"} <= rep.keys()
    # every row the report cites carries path + line evidence
    s = rep["structure"]
    for row in s["coupling"] + s["hubs"] + s["crowded_files"] + rep["recommendations"]:
        assert row["path"] and row["line"]
    assert "hotspots_error" in rep["risk"]  # temp copy is not a git repo
    md = report.to_markdown(rep)
    for heading in ("# Codebase report", "## Recommendations", "## Structure", "## Risk", "## Dead code", "## Limitations"):
        assert heading in md


def test_report_deadcode_is_verified(repo):
    from codebox import report
    d = report.deadcode(repo)
    assert {"unused", "stamp"} <= names(d["likely_dead"], "symbol")
    recs = report.build(repo)["recommendations"]
    assert any(r["kind"] == "delete_dead_code" and r["path"] == "util.py" for r in recs)


def test_report_kind_and_exclude(repo):
    from codebox import report
    rep = report.build(repo, "deadcode", exclude=("util.py",))
    assert "structure" not in rep and "recommendations" not in rep
    assert all(x["path"] != "util.py" for x in rep["deadcode"]["likely_dead"])
    with pytest.raises(ValueError):
        report.build(repo, "nope")


def test_report_html(repo, tmp_path):
    from codebox import report
    rep = report.build(repo)
    page = report.render(rep, "html")
    # Artifact page contract: no document skeleton of its own, <title> up front, theme tokens for dark mode
    assert page.startswith("<title>") and "<!doctype" not in page.lower() and "<body" not in page
    assert 'prefers-color-scheme: dark' in page and ':root[data-theme="dark"]' in page
    for heading in ("Recommendations", "Risk", "Structure", "Dead code", "Limitations"):
        assert f"<h2>{heading}</h2>" in page
    assert "util.py:" in page  # path:line evidence survives into the page
    out = report.write(rep, str(tmp_path / "r.html"))
    assert out.read_text() == page
    assert report.write(rep, str(tmp_path / "r.md")).read_text().startswith("# Codebase report")


def test_report_html_escapes():
    from codebox.report_html import inline
    assert inline("Split `<script>`") == "Split <code>&lt;script&gt;</code>"


def test_report_layers_and_distribution(repo):
    from codebox import report
    rep = report.build(repo)
    layers = {f: x["layer"] for x in rep["structure"]["layers"] for f in x["files"]}
    assert layers["app.py"] > layers["util.py"]  # app.py calls into util.py, so it sits above it
    dist = rep["risk"]["distribution"]
    assert sum(d["functions"] for d in dist) == rep["risk"]["functions_total"] > 0
    assert all(f["path"] and f["line"] for f in rep["risk"]["files"])


def test_layers_survive_cycles():
    from codebox.report import layers
    deps = [{"path": "a", "target": "b", "calls": 2}, {"path": "b", "target": "a", "calls": 1},
            {"path": "b", "target": "c", "calls": 1}]
    got = {f: x["layer"] for x in layers(deps) for f in x["files"]}
    assert set(got) == {"a", "b", "c"} and got["c"] == 0


def test_report_charts_render(repo):
    from codebox import report
    page = report.render(report.build(repo), "html")
    for label in ("Dependency layers", "Complexity distribution", "Size and complexity by file"):
        assert f"<h3>{label}</h3>" in page
    assert 'class="deps"' in page and 'class="tree"' in page and 'class="hist"' in page


def test_call_graph_panel(repo):
    import json, re
    from codebox import report
    rep = report.build(repo)
    cg = rep["structure"]["call_graph"]
    ids = {f["id"] for f in cg["functions"]}
    assert all(c["source"] in ids and c["target"] in ids for c in cg["calls"])
    assert any(c["source"].startswith("app.py#") and c["target"].startswith("util.py#twice") for c in cg["calls"])
    page = report.render(rep, "html")
    assert "<h3>Call graph</h3>" in page and "cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js" in page
    data = re.search(r'<script type="application/json" id="fg-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["calls"] == cg["calls"]
    assert any(f["source"] == "app.py" and f["target"] == "util.py" for f in cg["file_calls"])


def test_squarify_fills_area():
    from codebox.report_charts import _squarify
    vals = [50.0, 30.0, 15.0, 5.0]
    rects = _squarify([v * 600 * 320 / 100 for v in vals], 0, 0, 600, 320)
    assert abs(sum(w * h for _, _, w, h in rects) - 600 * 320) < 1e-6


def test_member_call_not_linked_to_nested_function(tmp_path):
    # m.group(1) on a regex match must not resolve to a function nested inside another function.
    (tmp_path / "a.py").write_text("def outer():\n    def group(x):\n        return x\n    return group(1)\n")
    (tmp_path / "b.py").write_text("import re\n\ndef parse(s):\n    m = re.match('x', s)\n    return m.group(1)\n")
    graph.build_index(str(tmp_path))
    assert names(graph.callers(str(tmp_path), "outer.group"), "caller") == {"outer"}


def test_plugin_skills_are_real_files():
    # Codex drops symlinks when it installs a plugin, so skills/ must hold real files that match the packaged skill.
    root = Path(__file__).parent.parent
    for d in (root / "skills").iterdir():
        assert not d.is_symlink() and (d / "SKILL.md").is_file(), d
    assert (root / "skills/codebox/SKILL.md").read_text() == (root / "src/codebox/skill/SKILL.md").read_text()


def test_coupling_matrix_uses_file_names():
    from codebox.report_html import _matrix
    page = _matrix([{"path": "src/pkg/a.py", "other": "src/pkg/b.py", "calls": 3},
                    {"path": "src/pkg/a.py", "other": "lib/b.py", "calls": 1}])
    assert '>a.py<' in page and 'title="src/pkg/a.py"' in page
    assert '>b.py<' not in page  # two b.py files: keep enough path to tell them apart


def _write(root: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    graph.build_index(str(root))
    return str(root)


def test_calls_never_cross_languages(tmp_path):
    # A TypeScript test calling run() must not resolve to a Python function that happens to be named run.
    repo = _write(tmp_path, {"svc/runner.py": "def run():\n    return 1\n",
                             "web/app.test.tsx": "it('x', () => { run(); });\n"})
    assert graph.callers(repo, "run") == []


def test_imported_name_resolves_to_its_module(tmp_path):
    # Three packages each define main(); `from pkg.cli.__main__ import main` must link to that one only.
    repo = _write(tmp_path, {
        "pkg/cli/__main__.py": "def main():\n    return 0\n",
        "pkg/web/__main__.py": "def main():\n    return 0\n",
        "pkg/worker/__main__.py": "def main():\n    return 0\n",
        "scripts/go.py": "from pkg.cli.__main__ import main\n\ndef go():\n    return main()\n",
        "scripts/mod.py": "from pkg.web import __main__ as web\n\ndef go():\n    return web.main()\n",
        "scripts/ext.py": "from os.path import join\n\ndef join_all():\n    return join('a', 'b')\n",
        "lib/paths.py": "def join(a, b):\n    return a + b\n"})
    rows = graph.run_query(repo, "MATCH (a:Symbol)-[r:CALLS]->(b:Symbol {name:'main'}) "
                                 "RETURN a.path AS src, b.path AS dst, r.confidence AS c ORDER BY src")
    assert [(r["src"], r["dst"], r["c"]) for r in rows] == [
        ("scripts/go.py", "pkg/cli/__main__.py", "imported"),
        ("scripts/mod.py", "pkg/web/__main__.py", "imported")]
    # join comes from os.path, not from the project's lib/paths.py
    assert graph.callers(repo, "join") == []


def test_report_leaves_test_callers_out(tmp_path):
    from codebox import report
    body = "".join(f"    step{i}()\n" for i in range(6))
    body += "".join(f"    x{i} = {i}\n" for i in range(45))  # real work, so the hub rule applies
    files = {"app/core.py": "".join(f"def step{i}():\n    return {i}\n\n" for i in range(6))
                            + f"def hub():\n{body}",
             "app/a.py": "from app.core import hub\n\ndef a():\n    hub()\n",
             "app/b.py": "from app.core import hub\n\ndef b():\n    hub()\n",
             "app/c.py": "from app.core import hub\n\ndef c():\n    hub()\n"}
    files |= {f"app/test_{i}.py": f"from app.core import hub\n\ndef test_{i}():\n    hub()\n" for i in range(5)}
    files |= {f"web/t{i}.test.tsx": "export {};\n" for i in range(2)}
    repo = _write(tmp_path, files)
    s = report.structure(repo)
    hub = next(h for h in s["hubs"] if h["symbol"] == "hub")
    assert hub["fan_in"] == 3  # a, b, c; the five test_*.py callers are left out
    assert all(not report._excluded(f["path"], report.DEFAULT_EXCLUDE) for f in s["call_graph"]["files"])
    assert not any(f["path"].endswith(".test.tsx") for f in s["call_graph"]["files"])
    rec = next(r for r in report.build(repo)["recommendations"] if r["kind"] == "decouple_hub")
    assert rec["cost"].startswith("3 caller file(s)")
    assert report._excluded("web/x.spec.ts", ("tests/",)) and not report._excluded("web/x.ts", ("tests/",))
    assert not report._excluded("app/test_0.py", ())  # an empty exclude analyses everything


def test_deadcode_skips_dunder_methods(tmp_path):
    from codebox import report
    repo = _write(tmp_path, {"m.py": "class A:\n    def __init__(self):\n        pass\n\n"
                                     "    def __call__(self):\n        pass\n"})
    d = report.deadcode(repo)
    assert not any(x["symbol"].endswith("__") for k in ("likely_dead", "mentioned_elsewhere")
                   for x in d[k])


def test_index_from_another_version_is_stale(tmp_path):
    import json
    repo = _write(tmp_path, {"a.py": "def f():\n    return 1\n"})
    assert not graph.is_stale(Path(repo))
    meta = Path(repo) / ".codebox" / "meta.json"
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "version": "0.0.1"}))
    assert graph.is_stale(Path(repo))


def test_thin_dispatcher_is_not_a_hub(tmp_path):
    # Same fan-in (3) and fan-out (5); only the one with real work behind it is worth decoupling.
    steps = "".join(f"def s{i}():\n    return {i}\n\n" for i in range(5))
    calls = "".join(f"    s{i}()\n" for i in range(5))
    filler = "".join(f"    x{i} = {i}\n" for i in range(45))
    callers = "".join(f"def c{i}():\n    thin()\n    fat()\n\n" for i in range(3))
    (tmp_path / "m.py").write_text(steps + f"def thin():\n{calls}\n" + f"def fat():\n{calls}{filler}\n" + callers)
    from codebox import report
    graph.build_index(str(tmp_path))
    recs = report.recommendations(str(tmp_path), report.structure(str(tmp_path), ()), None, None, exclude=())
    hubs = {r["what"] for r in recs if r["kind"] == "decouple_hub"}
    assert any("`fat`" in w for w in hubs) and not any("`thin`" in w for w in hubs)


def test_graph_script_is_ascii():
    # The page is ASCII-encoded with entities; inside <script> an entity would show up literally in tooltips.
    from codebox.report_graph import SCRIPT
    assert SCRIPT.isascii()
