"""Register the codebox MCP server and skill with coding agents (REA-style `setup`)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

HOME = Path.home()


@dataclass
class Plan:
    agent: str
    description: str
    apply: callable  # type: ignore[valid-type]


def server_command() -> list[str]:
    """How agents should launch the server. Prefer the installed `codebox` entry point."""
    exe = shutil.which("codebox")
    if exe:
        return [exe, "serve"]
    return [sys.executable, "-m", "codebox", "serve"]


def _skill_source() -> Path:
    return Path(str(resources.files("codebox") / "skill"))


def _backup(p: Path) -> None:
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak.{int(time.time())}"))


def _install_skill(dest_root: Path) -> None:
    dest = dest_root / "codebox"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_skill_source(), dest)


def _json_mcp(path: Path, key: str = "mcpServers") -> None:
    cmd = server_command()
    data = json.loads(path.read_text()) if path.exists() else {}
    _backup(path)
    data.setdefault(key, {})["codebox"] = {"command": cmd[0], "args": cmd[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def plans() -> list[Plan]:
    cmd = server_command()
    out: list[Plan] = []

    if shutil.which("claude"):
        def claude_apply():
            subprocess.run(["claude", "mcp", "remove", "-s", "user", "codebox"], capture_output=True)
            subprocess.run(["claude", "mcp", "add", "-s", "user", "codebox", "--", *cmd], check=True)
            _install_skill(HOME / ".claude" / "skills")
        out.append(Plan("claude-code", f"claude mcp add -s user codebox -- {' '.join(cmd)}; skill -> ~/.claude/skills/codebox", claude_apply))

    if shutil.which("codex") or (HOME / ".codex").exists():
        def codex_apply():
            cfg = HOME / ".codex" / "config.toml"
            text = cfg.read_text() if cfg.exists() else ""
            if "[mcp_servers.codebox]" not in text:
                _backup(cfg)
                cfg.parent.mkdir(parents=True, exist_ok=True)
                args = ", ".join(json.dumps(a) for a in cmd[1:])
                cfg.write_text(text.rstrip() + f'\n\n[mcp_servers.codebox]\ncommand = {json.dumps(cmd[0])}\nargs = [{args}]\n')
            _install_skill(HOME / ".codex" / "skills")
        out.append(Plan("codex", "append [mcp_servers.codebox] to ~/.codex/config.toml; skill -> ~/.codex/skills/codebox", codex_apply))

    if (HOME / ".cursor").exists():
        out.append(Plan("cursor", "add codebox to ~/.cursor/mcp.json", lambda: _json_mcp(HOME / ".cursor" / "mcp.json")))

    gemini = HOME / ".gemini" / "settings.json"
    if shutil.which("gemini") or gemini.parent.exists():
        out.append(Plan("gemini-cli", "add codebox to ~/.gemini/settings.json", lambda: _json_mcp(gemini)))

    claude_desktop = HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if claude_desktop.parent.exists():
        out.append(Plan("claude-desktop", "add codebox to claude_desktop_config.json", lambda: _json_mcp(claude_desktop)))

    return out


def run_setup(agents: list[str] | None, yes: bool, dry_run: bool) -> int:
    available = plans()
    if agents:
        available = [p for p in available if p.agent in agents]
    if not available:
        print("No supported agents detected (claude-code, codex, cursor, gemini-cli, claude-desktop).")
        return 1
    print("codebox setup will:")
    for p in available:
        print(f"  [{p.agent}] {p.description}")
    print("Existing config files are backed up as *.bak.<timestamp>.")
    if dry_run:
        return 0
    if not yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return 1
    rc = 0
    for p in available:
        try:
            p.apply()
            print(f"  ✓ {p.agent}")
        except Exception as e:
            rc = 1
            print(f"  ✗ {p.agent}: {e}")
    print("Restart your agents to pick up the new MCP server.")
    return rc
