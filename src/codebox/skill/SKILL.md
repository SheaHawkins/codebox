---
name: codebox
description: Static analysis of a codebase with the codebox MCP tools or CLI — repo maps, symbol lookup, call graphs (callers/callees/impact), structural ast-grep search, complexity, git hotspots, dead-code candidates and semgrep scans. Use when exploring an unfamiliar repo, planning a refactor or rename, estimating the blast radius of a change, hunting for a code pattern, or auditing code quality/security.
---

# codebox: static analysis workflow

codebox gives you a queryable model of a codebase instead of reading files one by one.
It is available as MCP tools (`mcp__codebox__*`) and as a CLI (`codebox …`, add `--json` for machine output).
If neither is installed, run it ad hoc: `uvx --from git+https://github.com/SheaHawkins/codebox codebox <cmd>`.

## Pick the right tool

| Question | MCP tool | CLI |
|---|---|---|
| What is this repo? Where is the weight? | `code_overview` | `codebox overview` |
| What's in this file? | `outline` | `codebox outline <file>` |
| Where is X defined? | `find_symbol` | `codebox find X` |
| Who calls X / what breaks if I change X? | `callers`, `impact` | `codebox callers X --depth 2`, `codebox impact X` |
| What does X depend on? | `callees` | `codebox callees X` |
| Find a code *shape* (not text) | `structural_search` | `codebox search 'pattern' -l python` |
| Find text / config / strings | `grep` | `codebox grep regex` |
| Where's the risky code? | `hotspots`, `complexity` | `codebox hotspots`, `codebox complexity` |
| Dead code? | `unreferenced` | `codebox unreferenced` |
| Bugs / security smells | `semgrep_scan` | `codebox scan --config p/default` |
| Anything else about structure | `graph_query` (+`graph_schema`) | `codebox query 'MATCH …'` |

## Workflow

1. **Orient**: `code_overview` first. It builds the graph on first use (stored in `<root>/.codebox/`, self-gitignored) and rebuilds automatically when files change.
2. **Narrow**: `find_symbol` → `outline` → read only the line ranges you need.
3. **Trace**: `callers`/`callees` with `depth` 1–3; `impact` before any change to a shared function.
4. **Pattern-hunt** with ast-grep. A `pattern` must parse as one complete node; `$X` = one node, `$$$X` = zero or more. When a shape isn't standalone (e.g. an `except` clause), use a `rule` object instead. Verified examples:
   - JS/TS dangerous eval: pattern `eval($X)`
   - Go ignored error: pattern `$A, _ := $F($$$)`
   - Python bare except: pattern `"try:\n    $$$B\nexcept:\n    $$$H"`
   - Python swallowed exception: rule `{"kind": "except_clause", "has": {"kind": "pass_statement", "stopBy": "end"}}`
   - TS await inside a loop: rule `{"pattern": "await $F($$$)", "inside": {"kind": "for_in_statement", "stopBy": "end"}}`
   If a pattern errors with "Multiple AST nodes", wrap it in its full parent construct or switch to a `rule`.
5. **Custom questions** → Cypher on LadybugDB, e.g.
   - Functions calling X but not Y: `MATCH (a:Symbol)-[:CALLS]->(:Symbol {name:'X'}) WHERE NOT EXISTS { MATCH (a)-[:CALLS]->(:Symbol {name:'Y'}) } RETURN a.qualname, a.path, a.start_line`
   - Cross-file coupling: `MATCH (a:Symbol)-[:CALLS]->(b:Symbol) WHERE a.path <> b.path RETURN a.path, b.path, count(*) AS n ORDER BY n DESC LIMIT 20`
   - Import users of a module: `MATCH (f:File)-[:IMPORTS]->(m:Module) WHERE m.name CONTAINS 'requests' RETURN f.path`

## Evidence and limits — read this

- Every result carries `path:line`. **Cite it** and open the code before asserting behavior.
- Call edges are **name-resolved** (no type inference). `confidence`: `same_file` > `imported` > `unique` > `ambiguous`. Method calls on objects (`obj.save()`) link to every `save` up to 3 candidates; beyond that they become `External`. For precise references in a typed language, confirm with the LSP tool if available.
- `unreferenced` lists candidates only: entrypoints, tests, exports, framework callbacks and reflection look unused.
- Semgrep fetches registry rules on first use (network); pass a local rules dir for offline.
