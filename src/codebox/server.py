"""MCP server exposing codebox tools over stdio."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import analyze, graph

INSTRUCTIONS = """codebox: static analysis for codebases.
Start with `code_overview` (auto-builds a LadybugDB code graph under <root>/.codebox).
Navigate with `find_symbol`, `outline`, `callers`, `callees`, `impact`; go deeper with `graph_query` (read-only Cypher; see `graph_schema`).
Use `structural_search` (ast-grep) instead of regex when matching code shapes; `grep` for text.
Quality/risk: `complexity`, `hotspots`, `unreferenced`, `semgrep_scan`.
Call edges are name-resolved (no type inference): check the `confidence` field and verify by reading code.
All paths are relative to `root` (default: server working directory)."""

mcp = MCPServer("codebox", instructions=INSTRUCTIONS)


def _ok(tool: str, result: Any, limitations: list[str] | None = None) -> dict:
    out = {"tool": tool, "result": result}
    if limitations:
        out["limitations"] = limitations
    return out


NAME_RESOLVED = ["Call edges resolved by name only; dynamic dispatch, aliases and overloaded names may be missed or over-linked."]


@mcp.tool()
def code_index(root: str | None = None) -> dict:
    """(Re)build the code graph for a repository. Usually automatic; call after large edits."""
    return _ok("code_index", graph.build_index(root))


@mcp.tool()
def code_overview(root: str | None = None, top: int = 15) -> dict:
    """Repo map: languages, sizes, most-called and most-calling symbols, top external calls."""
    return _ok("code_overview", graph.overview(root, top), NAME_RESOLVED)


@mcp.tool()
def outline(file: str, root: str | None = None) -> dict:
    """Symbols (classes, functions, methods) and imports of one file, with line ranges. No index needed."""
    return _ok("outline", analyze.outline(file, root))


@mcp.tool()
def find_symbol(query: str, kind: str | None = None, root: str | None = None, limit: int = 50) -> dict:
    """Find definitions whose qualified name contains `query` (case-insensitive). kind: function|method|class|..."""
    return _ok("find_symbol", graph.find_symbols(root, query, kind, limit))


@mcp.tool()
def callers(symbol: str, depth: int = 1, root: str | None = None, limit: int = 100) -> dict:
    """Who calls `symbol` (name, qualname like Class.method, or symbol id), up to `depth` hops."""
    return _ok("callers", graph.callers(root, symbol, depth, limit), NAME_RESOLVED)


@mcp.tool()
def callees(symbol: str, depth: int = 1, root: str | None = None, limit: int = 100) -> dict:
    """What `symbol` calls, up to `depth` hops, plus unresolved/external calls."""
    return _ok("callees", graph.callees(root, symbol, depth, limit), NAME_RESOLVED)


@mcp.tool()
def impact(symbol: str, depth: int = 4, root: str | None = None) -> dict:
    """Blast radius of changing `symbol`: all transitive callers grouped by file."""
    return _ok("impact", graph.impact(root, symbol, depth), NAME_RESOLVED)


@mcp.tool()
def graph_schema() -> str:
    """Node/relationship schema of the code graph, for writing `graph_query` Cypher."""
    return graph.schema_text()


@mcp.tool()
def graph_query(cypher: str, params: dict | None = None, root: str | None = None, limit: int = 200) -> dict:
    """Run a read-only Cypher query against the LadybugDB code graph. Use $params for values."""
    return _ok("graph_query", graph.run_query(root, cypher, params, limit))


@mcp.tool()
def unreferenced(root: str | None = None, limit: int = 100) -> dict:
    """Functions/methods with no known callers: dead-code candidates (verify before deleting)."""
    return _ok("unreferenced", graph.unreferenced(root, limit),
               NAME_RESOLVED + ["Entrypoints, tests, exported APIs, callbacks and reflection appear unreferenced."])


@mcp.tool()
def structural_search(language: str, pattern: str | None = None, rule: dict | None = None,
                      path: str | None = None, root: str | None = None, limit: int = 100) -> dict:
    """ast-grep structural search. pattern e.g. `fetch($URL)` / `except $E: pass`; $X = one node, $$$ = many.
    Or pass a full ast-grep `rule` object (kind/has/inside/not/all/any)."""
    return _ok("structural_search", analyze.structural_search(pattern, language, root, path, rule, limit))


@mcp.tool()
def grep(regex: str, path: str | None = None, glob: str | None = None, ignore_case: bool = False,
         root: str | None = None, limit: int = 100) -> dict:
    """ripgrep text search (respects .gitignore)."""
    return _ok("grep", analyze.grep(regex, root, path, glob, ignore_case, limit))


@mcp.tool()
def complexity(path: str | None = None, min_ccn: int = 10, root: str | None = None, limit: int = 50) -> dict:
    """Cyclomatic complexity (lizard). Functions with CCN >= min_ccn, worst first."""
    r = analyze.complexity(root, path, min_ccn, limit)
    r.pop("file_ccn", None)
    return _ok("complexity", r)


@mcp.tool()
def hotspots(days: int = 180, root: str | None = None, limit: int = 25) -> dict:
    """Git churn x complexity: files most likely to harbor bugs and worth refactoring first."""
    return _ok("hotspots", analyze.hotspots(root, days, limit))


@mcp.tool()
def semgrep_scan(config: str = "p/default", path: str | None = None, root: str | None = None, limit: int = 100) -> dict:
    """Semgrep rule scan (bugs/security). config: registry pack (p/default, p/owasp-top-ten, p/python...) or local rules path.
    Slow on first run; fetches rules from the registry unless config is local."""
    return _ok("semgrep_scan", analyze.semgrep_scan(root, path, config, limit))


def main() -> None:
    mcp.run("stdio")
