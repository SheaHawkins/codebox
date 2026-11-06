---
description: Generate a visual codebox report (structure, risk, dead code, ranked recommendations) and open it as an Artifact
argument-hint: "[all|structure|risk|deadcode] [path/to/report.md]"
---

Generate a codebox report for the current repository and show it visually.

Arguments: `$ARGUMENTS`
- The first argument, if it is one of `all`, `structure`, `risk` or `deadcode`, is the report kind. Default: `all`.
- An argument ending in `.md` asks for a Markdown copy at that path as well.

Steps:

1. Call the codebox `code_report` MCP tool with that `kind`, `format: "html"` and `output: ".codebox/report.html"`. `.codebox/` is already ignored by git, so the report never shows up as a change. If the repo keeps tests or fixtures somewhere other than `tests/`, `test/`, `testdata/` or `fixtures/`, pass those prefixes as `exclude`. If a Markdown copy was asked for, call it again with `format: "markdown"` and that `output`.
   If the MCP tool isn't available, run `codebox report <kind> -o .codebox/report.html` instead, or `uvx --from git+https://github.com/SheaHawkins/codebox codebox report <kind> -o .codebox/report.html` if the CLI isn't installed.
2. For an `all` report, check each of the top three recommendations (returned in the tool result) against the code it cites. Open the `path:line` and confirm the problem is real. Don't edit the report.
3. Show the page:
   - If you have the Artifact tool, read `.codebox/report.html` in full, then publish it with `icon: "chart"` and a one-sentence `description` naming the repo. The page is already written to the Artifact page contract, so publish it unchanged. If you already published a codebox report for this repo earlier in this session, publish to that same artifact so the link stays the same.
   - Otherwise, give the absolute path of `.codebox/report.html` and the command to open it (`open` on macOS, `xdg-open` on Linux, `start` on Windows).
4. Reply with:
   - the artifact link, or the file path;
   - one line of size stats (files, symbols);
   - the top three recommendations, each with its `path:line` and one sentence on whether it holds up after reading the code;
   - anything the report got wrong, such as a "likely dead" function that is actually used.

Don't change any code. The report is for deciding what to do next.
