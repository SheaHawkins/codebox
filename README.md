# codebox

**Static analysis toolbox for coding agents.** One package that is simultaneously a **CLI**, an **MCP server**, and an **agent skill**, modeled on [REA](https://github.com/morluto/rea)'s layout.

It gives Claude (or Codex, Cursor, Gemini CLI…) a queryable model of a codebase instead of making it read files one at a time:

| Layer | Tool | What it provides |
|---|---|---|
| Parsing | [tree-sitter](https://tree-sitter.github.io) via `tree-sitter-language-pack` | Outlines, imports and call sites for 300+ languages (grammars download on first use) |
| Code graph | [LadybugDB](https://github.com/LadybugDB/ladybug) (embedded Cypher graph DB, the Kùzu successor) | `File`/`Symbol`/`Module` graph with `CALLS`, `CONTAINS`, `IMPORTS` edges; callers, callees, impact, arbitrary read-only Cypher |
| Structural search | [ast-grep](https://ast-grep.github.io) | Match code *shapes* (`eval($X)`, `$A, _ := $F($$$)`) and full rule objects |
| Text search | [ripgrep](https://github.com/BurntSushi/ripgrep) | Fast, `.gitignore`-aware grep |
| Metrics | [lizard](https://github.com/terryyin/lizard) + git | Cyclomatic complexity; churn × complexity **hotspots** |
| Rules | [semgrep](https://semgrep.dev) (via `uvx`, not a hard dependency) | Bug and security scans from registry packs or local rules |

Every result carries `path:line` evidence, and tools state their limitations, such as name-based call resolution.

## Quick start

### Plugin (recommended)

This repo is a plugin marketplace. Installing the plugin gives your agent the MCP server and the skill together. The server runs through `uv`, so you need [uv](https://docs.astral.sh/uv/) on your `PATH`.

**Claude Code**

```bash
claude plugin marketplace add SheaHawkins/codebox
claude plugin install codebox@codebox
```

Or, inside a session: `/plugin marketplace add SheaHawkins/codebox`, then `/plugin install codebox@codebox`.

**Codex**

```bash
codex plugin marketplace add SheaHawkins/codebox
codex plugin add codebox@codebox
```

Restart the agent after installing. The first tool call builds the code graph.

**Updating:** refreshing the marketplace only updates the catalog. You also have to update the plugin itself, then restart:

```bash
claude plugin marketplace update codebox && claude plugin update codebox@codebox
codex plugin marketplace upgrade && codex plugin add codebox@codebox
```

In Claude Code, `/codebox:report` saves a full report to `codebox-report.md` and checks the top recommendations against the code (`/codebox:report structure`, `risk` or `deadcode` for one part). In any agent, you can also just ask for a codebox report.

### CLI with uv

Install the CLI to use codebox from a terminal, or to register it with other agents (Cursor, Gemini CLI, …). It needs Python 3.11–3.14.

```bash
uv tool install git+https://github.com/SheaHawkins/codebox   # or, from a checkout: uv tool install .

codebox setup      # register the MCP server + skill with every agent it detects; shows the plan, asks first, backs up configs
                   # --dry-run to preview, --agent claude-code to limit
codebox doctor     # check rg / git / semgrep; --prefetch python typescript go
```

To run it once without installing: `uvx --from git+https://github.com/SheaHawkins/codebox codebox overview`.

**Skill only:** `skills/codebox/SKILL.md` works on its own. When the CLI isn't installed, it runs codebox through `uvx`.

## CLI

```bash
codebox overview                       # repo map: languages, biggest files, most-called/most-calling symbols
codebox outline src/app.py
codebox find Service --kind class
codebox callers helper --depth 2       # call-site lines included
codebox callees Service.run
codebox impact validateGraph           # transitive callers grouped by file
codebox query "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) WHERE a.path <> b.path
               RETURN a.path, b.path, count(*) AS n ORDER BY n DESC LIMIT 10"
codebox search 'JSON.parse($X)' -l typescript
codebox grep 'TODO|FIXME'
codebox complexity --min-ccn 15
codebox hotspots --days 90
codebox unreferenced                   # dead-code candidates
codebox scan --config p/owasp-top-ten
codebox report -o report.md            # structure + risk + dead code, with ranked recommendations
codebox report structure               # or: risk, deadcode; --exclude PREFIX, --json
```

Global flags go anywhere: `-C/--root <repo>` and `--json`.

The graph is stored in `<repo>/.codebox/`, which ignores itself in git. It rebuilds automatically when any source file is newer than the index.

## MCP tools

`code_overview`, `code_index`, `code_report`, `outline`, `find_symbol`, `callers`, `callees`, `impact`, `graph_schema`, `graph_query`, `unreferenced`, `structural_search`, `grep`, `complexity`, `hotspots`, `semgrep_scan`. Run the server manually with `codebox serve`, which uses stdio.

## Graph schema

```
(:File {path, language, lines, code_lines, parse_errors})
(:Symbol {id, name, qualname, kind, path, start_line, end_line, signature})
(:External {name})   (:Module {name})
(File)-[:DEFINES]->(Symbol)          (Symbol)-[:CONTAINS]->(Symbol)
(Symbol)-[:CALLS {line, confidence}]->(Symbol)
(Symbol)-[:CALLS_EXTERNAL {line}]->(External)
(File)-[:IMPORTS {line, items}]->(Module)
```

Code at module level is attributed to a pseudo-symbol named `<module>`.

## Accuracy model

Calls are resolved **by name**, without type inference, in this order: same file → imported name → globally unique → ambiguous (up to 3 candidates). The edge's `confidence` property records which rule matched. A receiver call (`obj.save()`) only links to methods. A bare call (`save()`) prefers top-level functions, so `Date.now()` never resolves to your own `now()`. If you need precise references in typed languages, pair codebox with an LSP.

## Development

```bash
uv sync && uv run pytest
```

Layout: `src/codebox/{discover,parse,graph,analyze,report,server,cli,setup_agents}.py`. The skill lives in `src/codebox/skill/` so that it ships inside the wheel; `skills/codebox` is a symlink to it for plugins and skills.sh.

## Roadmap

- Incremental indexing (re-parse only changed files, keyed on content hash)
- Import → file resolution (`IMPORTS` to `File`, not only `Module`) for module dependency graphs and cycle detection
- More analyzers: `osv-scanner` (vulnerable dependencies), `gitleaks` (secrets), `jscpd` (duplication), `tokei`/`scc`
- Optional embeddings in LadybugDB's vector index for semantic code search
- SARIF output for CI
