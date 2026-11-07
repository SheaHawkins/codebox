---
name: codebox
description: Static analysis of a codebase with the codebox MCP tools or CLI — repo maps, symbol lookup, call graphs (callers/callees/impact), structural ast-grep search, complexity, git hotspots, coupling, dead-code candidates, semgrep scans, and Markdown reports that combine them. Use it to recommend structural changes that reduce complexity and coupling or improve how code is organized (what to split, move, merge or decouple, backed by metrics), before you change a function other code depends on (run impact first), when you would otherwise open more than a handful of files to understand a repo, when searching for a code shape that regex gets wrong, and when auditing for risk or dead code. Skip it for small repos, single-file edits, and plain text or config searches.
---

# codebox: static analysis workflow

codebox gives you a queryable model of a codebase instead of reading files one by one.
It is available as MCP tools (`mcp__codebox__*`) and as a CLI (`codebox …`, add `--json` for machine output).
If neither is installed, run it ad hoc: `uvx --from git+https://github.com/SheaHawkins/codebox codebox <cmd>`.

## When to reach for it

**Use it:**
- **When asked how to improve the structure of a codebase**, or what to refactor, split, or clean up. Follow "Recommending structural changes" below. Recommendations backed by complexity, coupling and churn numbers beat ones based on a read-through, because a reader can't see fan-in, cycles or churn.
- **Before changing a function other code depends on.** Run `impact` before you rename, change a signature, or change behavior. Reading call sites by hand misses the ones in files you didn't think to open. This is the most valuable thing codebox does.
- **When you'd otherwise open more than ~5 files to get your bearings.** `code_overview` + `find_symbol` + `outline` cost less context than reading files whole. Read only the line ranges they point you at.
- **When the question is about structure:** who calls this, what does this reach, which files are coupled. grep finds where a name appears as text. It can't tell a call from a comment or a same-named method.
- **When you're matching a code shape:** an unchecked error, `await` in a loop, a swallowed exception. Use `structural_search`. Regex gives false hits on code like this; ast-grep doesn't.
- **When asked where the risk or the dead code is.** `hotspots` (churn × complexity) is a better answer to "what should we refactor first" than complexity alone.

**Don't use it:**
- **Small repos (under ~20 source files) or a task inside one file you already know.** Just read the code. Building the graph and querying it is overhead you don't need.
- **Text, config, strings, log messages, TODOs.** Use your normal search tool, or `grep` here. The graph only knows about definitions and calls.
- **Precise references in a typed language,** for example "every implementation of this interface" or "every call through this type". The graph resolves calls by name. Use an LSP when one is available, and codebox to find where to look.
- **Deciding on its own that code can be deleted.** `unreferenced` is a list of leads, and in a typical repo most of them are false positives. See below.
- **Runtime questions:** what a value is, which branch runs, why a test fails. Static analysis can't answer these. Run the code.

## Pick the right tool

| Question | MCP tool | CLI |
|---|---|---|
| What is this repo? Where is the weight? | `code_overview` | `codebox overview` |
| What's in this file? | `outline` | `codebox outline <file>` |
| Where is X defined? | `find_symbol` | `codebox find X` |
| What breaks if I change X? | `impact` | `codebox impact X` |
| Who calls X? | `callers` | `codebox callers X --depth 2` |
| What does X depend on? | `callees` | `codebox callees X` |
| Find a code *shape* (not text) | `structural_search` | `codebox search 'pattern' -l python` |
| Find text / config / strings | `grep` | `codebox grep regex` |
| Where's the risky code? | `hotspots`, then `complexity` | `codebox hotspots`, `codebox complexity` |
| Dead code? | `unreferenced`, then verify each | `codebox unreferenced` |
| Bugs / security smells | `semgrep_scan` | `codebox scan --config p/default` |
| Whole-repo report + ranked restructuring recommendations | `code_report` (`format: "html"` + `output` for a visual page) | `codebox report [structure\|risk\|deadcode] -o report.html` |
| Anything else about structure | `graph_query` (+`graph_schema`) | `codebox query 'MATCH …'` |

## Workflow

1. **Orient**: `code_overview` first. It builds the graph on first use (stored in `<root>/.codebox/`, self-gitignored) and rebuilds automatically when files change. Its `most_called` list tells you which functions are the riskiest to change.
2. **Narrow**: `find_symbol` → `outline` → read only the line ranges you need.
3. **Trace**: `callers`/`callees` with `depth` 1–3. Run `impact` before any change to a shared function, and include its `by_file` list in your plan so the user can see the blast radius.
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

## Recommending structural changes

The goal is a short, ranked list of changes, each of which measurably reduces complexity or coupling or puts code where it belongs. Every recommendation must rest on numbers you gathered, not on taste.

### 1. Gather the evidence

Start with `code_report` (CLI: `codebox report`). It runs everything below in one call and returns a Markdown report with a first-pass ranked list. Treat that list as a draft: it applies fixed thresholds, so open the code behind each item, drop what doesn't hold up, and add what the numbers can't see. Use `kind` to get one part (`structure`, `risk`, `deadcode`), and `exclude` to match the repo's test and fixture layout. When the user wants to see the report, pass `format: "html"` and `output: ".codebox/report.html"`, then publish that file as an Artifact if you have the Artifact tool (it already meets the page contract), or give them the path to open.

To dig further, or to run one check on its own, use the tools and queries below.

- `code_overview`: size, largest files, `most_called` and `most_calling` symbols.
- `complexity` with `min_ccn` 10: functions too branchy to follow.
- `hotspots`: files that are both complex and frequently changed. These are where a fix pays off most.
- The Cypher queries below. They skip `ambiguous` edges and `tests/`, because those edges are often false links. A common name like `run` or `get` can wire unrelated code together and invent coupling that isn't there. Change the path filter to match the repo's test and fixture layout.

**File coupling:** pairs of files that call each other most, counted in both directions:
```
MATCH (a:Symbol)-[r:CALLS]->(b:Symbol)
WHERE r.confidence <> 'ambiguous' AND a.path <> b.path
  AND NOT a.path STARTS WITH 'tests/' AND NOT b.path STARTS WITH 'tests/'
WITH CASE WHEN a.path < b.path THEN a.path ELSE b.path END AS x,
     CASE WHEN a.path < b.path THEN b.path ELSE a.path END AS y
RETURN x, y, count(*) AS calls ORDER BY calls DESC LIMIT 15
```

**File cycles:** two files that each call into the other:
```
MATCH (a:Symbol)-[r1:CALLS]->(b:Symbol), (c:Symbol)-[r2:CALLS]->(d:Symbol)
WHERE r1.confidence <> 'ambiguous' AND r2.confidence <> 'ambiguous'
  AND a.path = d.path AND b.path = c.path AND a.path < b.path
RETURN DISTINCT a.path AS file_a, b.path AS file_b
```

**Hub functions:** many callers *and* many callees, so a change ripples both ways:
```
MATCH (c:Symbol)-[r1:CALLS]->(s:Symbol)-[r2:CALLS]->(d:Symbol)
WHERE s.kind <> 'module' AND r1.confidence <> 'ambiguous' AND r2.confidence <> 'ambiguous'
  AND NOT s.path STARTS WITH 'tests/'
RETURN s.qualname, s.path, s.start_line, count(DISTINCT c) AS fan_in, count(DISTINCT d) AS fan_out
ORDER BY fan_in * fan_out DESC LIMIT 15
```

**Misplaced functions:** called repeatedly, but only from one other file:
```
MATCH (c:Symbol)-[r:CALLS]->(s:Symbol)
WHERE s.kind IN ['function','method'] AND r.confidence <> 'ambiguous'
WITH s, count(*) AS total, collect(DISTINCT c.path) AS from_files
WHERE size(from_files) = 1 AND from_files[1] <> s.path AND total >= 2
RETURN s.qualname, s.path, s.start_line, from_files[1] AS only_caller_file, total ORDER BY total DESC
```

**Split seams in a crowded file:** which other files use each of its symbols. Groups of symbols with different users are where the file wants to split:
```
MATCH (f:File {path:$path})-[:DEFINES]->(s:Symbol) WHERE s.kind IN ['function','method','class']
OPTIONAL MATCH (c:Symbol)-[r:CALLS]->(s) WHERE r.confidence <> 'ambiguous' AND c.path <> s.path
RETURN s.qualname AS symbol, s.start_line AS line, collect(DISTINCT c.path) AS used_from ORDER BY line
```
(With `graph_query`, pass `params: {"path": "src/x.py"}`. The CLI has no params, so write the path into the query in place of `$path`.)

**Crowded files:** symbol counts per file:
```
MATCH (f:File)-[:DEFINES]->(s:Symbol) WHERE s.kind IN ['function','method','class']
RETURN f.path, f.code_lines, count(s) AS symbols ORDER BY symbols DESC LIMIT 15
```

### 2. Turn signals into recommendations

| Signal | Usual recommendation |
|---|---|
| Function with CCN ≥ 15, especially in a hotspot file | Split it into named steps. Each branch-heavy block becomes a function whose name says what it decides. Table-driven dispatch can replace long if/elif chains. |
| Hub function (high fan-in × fan-out) | Separate the stable interface callers depend on from the orchestration behind it, so internal changes stop rippling out to callers. |
| File cycle | Move the shared piece into a third module both depend on, or invert one direction with a callback or interface. |
| Heavily coupled file pair | If they always change together, consider merging them. If they're separate concerns, add a narrow interface between them and route calls through it. |
| Misplaced function | Move it next to its only caller. If it's private to that caller, nest it or make it module-private there. |
| Crowded file whose split-seam query shows distinct user groups | Split the file along those groups. Build-time and query-time code, or I/O and pure logic, are common seams. |
| Dead code (verified, see below) | Delete it. |

**Don't flag these as problems:**
- **Routers, dispatchers, CLI `main`s and registries:** high fan-out is their job.
- **Entrypoints, tests and generated code.**
- **Code that's complex but stable:** low churn and no planned work means the refactor costs more than it saves.

### 3. Present the recommendations

For each recommendation, give:
- **What:** the concrete change ("split `build_index` (graph.py:97) into parse, resolve and load steps").
- **Evidence:** the numbers and `path:line` that justify it (CCN 51, top hotspot, fan-out 10).
- **Gain:** what gets simpler and which metric should drop.
- **Cost:** from `impact`, how many call sites and files the change touches.

Rank by gain against cost, and put hotspot fixes first. Keep the list short. Three well-founded changes are more useful than fifteen nitpicks. Don't start restructuring until the user picks what to do. After a change is made, re-run the same queries and report the before and after numbers.

## Dead code: treat every result as a lead

`unreferenced` reports functions with no call edge pointing at them. These look unused but usually aren't:
- functions registered through a decorator or framework: MCP/HTTP handlers, CLI commands, pytest tests and fixtures;
- functions stored and called later: callbacks, dispatch tables, closures passed as values;
- exported APIs, entrypoints, and anything reached through reflection or `getattr`.

Before you call something dead, grep for its name across the repo, including strings and config. Report only what survives that check. Never delete code based on `unreferenced` alone. `code_report` with `kind: "deadcode"` does the decorator and grep checks for you and sorts the candidates into likely dead, decorator-registered, and mentioned elsewhere.

## Evidence and limits — read this

- Every result carries `path:line`. **Cite it** and open the code before asserting behavior.
- Call edges are **name-resolved** (no type inference). `confidence`: `same_file` > `imported` > `unique` > `ambiguous`. Calls through an imported project module (`util.helper()`) resolve with `imported`. Method calls on objects (`obj.save()`) link to every `save` up to 3 candidates; beyond that they become `External`. Treat `ambiguous` edges as "maybe" and check them.
- An empty `callers` result means no call was found by name. It doesn't prove there are no callers. Grep before relying on it.
- Semgrep fetches registry rules on first use (network); pass a local rules dir for offline.
