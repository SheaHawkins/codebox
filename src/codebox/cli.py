"""codebox CLI: same operations as the MCP server, for humans and scripts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import analyze, graph, report


def _print(obj, as_json: bool) -> None:
    if as_json or not isinstance(obj, (list, dict)):
        print(json.dumps(obj, indent=2, default=str) if not isinstance(obj, str) else obj)
        return
    rows = obj if isinstance(obj, list) else None
    if rows is None:
        for k in ("matches", "functions", "hotspots", "findings", "symbols"):
            if isinstance(obj.get(k), list):
                rows = obj[k]
                break
    if rows is None:
        print(json.dumps(obj, indent=2, default=str))
        return
    for r in rows:
        loc = f'{r.get("path", "")}:{r.get("line", "")}'.strip(":")
        rest = {k: v for k, v in r.items() if k not in ("path", "line", "id")}
        print(f"{loc:<50} " + "  ".join(f"{k}={v}" for k, v in rest.items()))
    if isinstance(obj, dict) and obj.get("truncated"):
        print("… truncated (raise --limit)")


def doctor(prefetch: list[str]) -> int:
    import tree_sitter_language_pack as tslp
    checks = {
        "ripgrep (rg)": shutil.which("rg"),
        "git": shutil.which("git"),
        "semgrep": shutil.which("semgrep") or (shutil.which("uvx") and "via uvx"),
        "uvx": shutil.which("uvx"),
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}: {ok or 'missing'}")
    print(f"  ✓ tree-sitter grammars: {tslp.language_count()} available, cached: {', '.join(tslp.downloaded_languages()) or 'none'}")
    print(f"    cache dir: {tslp.cache_dir()}")
    if prefetch:
        tslp.download(prefetch)
        print(f"  ✓ prefetched: {', '.join(prefetch)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codebox", description="Static analysis toolbox for coding agents.")
    ap.add_argument("--root", "-C", default=None, help="repository root (default: cwd)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the MCP server on stdio")
    s = sub.add_parser("setup", help="register MCP server + skill with your agents")
    s.add_argument("--agent", action="append", help="limit to agent(s): claude-code, codex, cursor, gemini-cli, claude-desktop")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("doctor", help="check external tools; optionally prefetch grammars")
    d.add_argument("--prefetch", nargs="*", default=[], help="languages to download grammars for")

    sub.add_parser("index", help="build the code graph")
    o = sub.add_parser("overview", help="repo map from the code graph")
    o.add_argument("--top", type=int, default=15)
    o = sub.add_parser("outline", help="symbols and imports of a file")
    o.add_argument("file")
    f = sub.add_parser("find", help="find symbol definitions")
    f.add_argument("query")
    f.add_argument("--kind")
    for name in ("callers", "callees", "impact"):
        p = sub.add_parser(name, help=f"{name} of a symbol")
        p.add_argument("symbol")
        p.add_argument("--depth", type=int, default=4 if name == "impact" else 1)
    q = sub.add_parser("query", help="read-only Cypher against the graph")
    q.add_argument("cypher")
    sub.add_parser("schema", help="print graph schema")
    sub.add_parser("unreferenced", help="dead-code candidates")
    sg = sub.add_parser("search", help="ast-grep structural search")
    sg.add_argument("pattern")
    sg.add_argument("--lang", "-l", required=True)
    sg.add_argument("--path")
    g = sub.add_parser("grep", help="ripgrep text search")
    g.add_argument("regex")
    g.add_argument("--path")
    g.add_argument("--glob")
    g.add_argument("-i", action="store_true")
    c = sub.add_parser("complexity", help="cyclomatic complexity (lizard)")
    c.add_argument("--path")
    c.add_argument("--min-ccn", type=int, default=10)
    h = sub.add_parser("hotspots", help="git churn x complexity")
    h.add_argument("--days", type=int, default=180)
    sc = sub.add_parser("scan", help="semgrep scan")
    sc.add_argument("--config", default="p/default")
    sc.add_argument("--path")
    rp = sub.add_parser("report", help="Markdown report: structure, risk, dead code and ranked recommendations")
    rp.add_argument("kind", nargs="?", default="all", choices=report.KINDS)
    rp.add_argument("--output", "-o", help="write the report to this file instead of stdout (.html/.json/.md picks the format)")
    rp.add_argument("--format", "-f", choices=("markdown", "html", "json"), help="output format (default: markdown, or from -o's extension)")
    rp.add_argument("--exclude", action="append",
                    help=f"path prefix to leave out (repeatable; default: {' '.join(report.DEFAULT_EXCLUDE)})")
    rp.add_argument("--top", type=int, default=15, help="rows per table")
    rp.add_argument("--days", type=int, default=180, help="git history window for hotspots")
    for p in sub.choices.values():
        p.add_argument("--limit", type=int, default=50)
        # accept global flags after the subcommand too
        p.add_argument("--root", "-C", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    a = ap.parse_args(argv)
    r, j = a.root, a.json

    if a.cmd == "serve":
        from .server import main as serve
        serve()
        return 0
    if a.cmd == "setup":
        from .setup_agents import run_setup
        return run_setup(a.agent, a.yes, a.dry_run)
    if a.cmd == "doctor":
        return doctor(a.prefetch)
    if a.cmd == "report":
        return _report(a, r, j)

    try:
        result = _dispatch(a, r)
    except Exception as e:  # clean, agent-readable failure
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if a.cmd == "outline" and not j:
        for i in result["imports"]:
            print(f'{result["path"]}:{i["line"]:<6} import  {i["source"]}')
        result = [{"path": result["path"], **s} for s in result["symbols"]]
    _print(result, j)
    return 0


def _report(a, r, as_json: bool) -> int:
    fmt = a.format or ("json" if as_json else None)
    try:
        rep = report.build(r, a.kind, tuple(a.exclude) if a.exclude else report.DEFAULT_EXCLUDE, a.top, a.days)
        if a.output:
            print(f"wrote {report.write(rep, a.output, fmt, r)}", file=sys.stderr)
        else:
            print(report.render(rep, fmt or "markdown"))
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


def _dispatch(a, r):
    return {
        "index": lambda: graph.build_index(r),
        "overview": lambda: graph.overview(r, a.top),
        "outline": lambda: analyze.outline(a.file, r),
        "find": lambda: graph.find_symbols(r, a.query, a.kind, a.limit),
        "callers": lambda: graph.callers(r, a.symbol, a.depth, a.limit),
        "callees": lambda: graph.callees(r, a.symbol, a.depth, a.limit),
        "impact": lambda: graph.impact(r, a.symbol, a.depth),
        "query": lambda: graph.run_query(r, a.cypher, limit=a.limit),
        "schema": graph.schema_text,
        "unreferenced": lambda: graph.unreferenced(r, a.limit),
        "search": lambda: analyze.structural_search(a.pattern, a.lang, r, a.path, None, a.limit),
        "grep": lambda: analyze.grep(a.regex, r, a.path, a.glob, a.i, a.limit),
        "complexity": lambda: {k: v for k, v in analyze.complexity(r, a.path, a.min_ccn, a.limit).items() if k != "file_ccn"},
        "hotspots": lambda: analyze.hotspots(r, a.days, a.limit),
        "scan": lambda: analyze.semgrep_scan(r, a.path, a.config, a.limit),
    }[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
