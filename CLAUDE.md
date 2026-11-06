# codebox

Python 3.11+ package managed with uv. One codebase exposes three surfaces: the CLI (`cli.py`), the MCP server (`server.py`), and the skill (`src/codebox/skill/SKILL.md`, symlinked at `skills/codebox`).

- `uv run pytest` must pass. Tests index `tests/fixtures/sample` in a temp dir.
- When you add a capability, wire it into the CLI, the MCP server, and the skill table together.
- Every result must carry `path` and `line` evidence. If a tool is heuristic, say so in its `limitations`.
- Never write `.codebox/` into a repo you're only testing against. Copy it into a temp dir first.
- mcp is v2: `from mcp.server.mcpserver import MCPServer`. FastMCP is gone.
- Ladybug Python API (Kùzu-compatible): `ladybug.Database(path, read_only=...)`, `Connection.execute(cypher, params)`.
- Bump the version in `.claude-plugin/plugin.json`, `pyproject.toml` and `src/codebox/__init__.py` (then `uv lock`) whenever a change should reach plugin users. `claude plugin update` and `codex plugin add` compare versions, so a new commit with the same version is never installed.
