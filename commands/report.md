---
description: Generate a codebox report (structure, risk, dead code, ranked recommendations) and save it as Markdown
argument-hint: "[all|structure|risk|deadcode] [path/to/output.md]"
---

Generate a codebox report for the current repository.

Arguments: `$ARGUMENTS`
- The first argument, if it is one of `all`, `structure`, `risk` or `deadcode`, is the report kind. Default: `all`.
- An argument ending in `.md` is the output file. Default: `codebox-report.md` in the repository root.

Steps:

1. Call the codebox `code_report` MCP tool with that `kind`. If the repo keeps tests or fixtures somewhere other than `tests/`, `test/`, `testdata/` or `fixtures/`, pass those prefixes as `exclude`. If the MCP tool isn't available, run `codebox report <kind> -o <file>` instead, or `uvx --from git+https://github.com/SheaHawkins/codebox codebox report <kind> -o <file>` if the CLI isn't installed.
2. Write the returned `markdown` to the output file unchanged.
3. For an `all` report, check each of the top three recommendations against the code it cites. Open the `path:line` and confirm the problem is real. Don't edit the saved report.
4. Reply with:
   - the path of the saved file;
   - one line of size stats (files, symbols);
   - the top three recommendations, each with its `path:line` and one sentence on whether it holds up after reading the code;
   - anything the report got wrong, such as a "likely dead" function that is actually used.

Don't change any code. The report is for deciding what to do next.
