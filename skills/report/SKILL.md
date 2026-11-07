---
name: report
description: Generate a visual codebox report for the current repository (structure, risk, dead code, ranked restructuring recommendations, interactive call graph), check its top recommendations against the code, and show it as an Artifact or an HTML file. Use when the user asks for a codebox report, a code health or architecture report, or runs /codebox:report.
argument-hint: "[all|structure|risk|deadcode] [path/to/report.md]"
---

# codebox report

Generate a codebox report for the current repository and show it visually.

Arguments come from the user's request (in Claude Code, also from `$ARGUMENTS`):
- A report kind, if they name one of `all`, `structure`, `risk` or `deadcode`. Default: `all`.
- A path ending in `.md`, if they want a Markdown copy at that path as well.

## Steps

1. **Generate it.** Call the codebox `code_report` MCP tool with that `kind`, `format: "html"` and `output: ".codebox/report.html"`. `.codebox/` is already ignored by git, so the report never shows up as a change. If the repo keeps tests or fixtures somewhere other than `tests/`, `test/`, `testdata/` or `fixtures/`, pass those prefixes as `exclude`. If a Markdown copy was asked for, call it again with `format: "markdown"` and that `output`.
   If the MCP tool isn't available, run `codebox report <kind> -o .codebox/report.html`, or `uvx --from git+https://github.com/SheaHawkins/codebox codebox report <kind> -o .codebox/report.html` if the CLI isn't installed.
2. **Check it.** For an `all` report, check each of the top three recommendations (returned in the tool result) against the code it cites: open the `path:line` and confirm the problem is real. Don't edit the report.
3. **Show it.**
   - If you have an Artifact tool (claude.ai Artifacts), read `.codebox/report.html` in full, then publish it with `icon: "chart"` and a one-sentence `description` naming the repo. The page is already written to the Artifact page contract, so publish it unchanged. If you already published a codebox report for this repo earlier in the session, publish to that same artifact so the link stays the same.
   - Otherwise (Codex, or no Artifact tool), give the absolute path of `.codebox/report.html` and the command to open it: `open` on macOS, `xdg-open` on Linux, `start` on Windows. The page is self-contained apart from web fonts and d3 for the call graph.
4. **Reply** with:
   - the artifact link, or the file path;
   - one line of size stats (files, symbols);
   - the top three recommendations, each with its `path:line` and one sentence on whether it holds up after reading the code;
   - **Checked and correct:** other findings you verified and found right, such as dead code that really is unused;
   - **Wrong:** only findings that turned out false, such as a "likely dead" function that is actually used, or a hub whose callers are mostly tests. If nothing was wrong, say so in one line. Never put a confirmed finding under this heading.

Don't change any code. The report is for deciding what to do next.
