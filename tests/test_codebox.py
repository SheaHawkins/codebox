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
