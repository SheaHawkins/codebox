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
