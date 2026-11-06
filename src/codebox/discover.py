"""Find source files in a repo, respecting .gitignore when possible."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import tree_sitter_language_pack as tslp

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist",
    "build", "target", ".next", ".tox", ".mypy_cache", ".pytest_cache", ".codebox",
    "vendor", ".idea", ".vscode",
}
MAX_BYTES = 1_000_000

# Languages we treat as data/markup, not code worth graphing.
NON_CODE = {"json", "yaml", "toml", "markdown", "csv", "xml", "html", "css", "ini", "dockerfile", "make"}


def resolve_root(root: str | os.PathLike | None) -> Path:
    return Path(root or os.getcwd()).expanduser().resolve()


def _list_paths(root: Path) -> list[str]:
    if (root / ".git").exists() and shutil.which("git"):
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            return [p for p in out.stdout.split("\0") if p]
    if shutil.which("rg"):
        out = subprocess.run(["rg", "--files", str(root)], capture_output=True, text=True)
        if out.returncode in (0, 1):
            return [os.path.relpath(p, root) for p in out.stdout.splitlines()]
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            paths.append(os.path.relpath(os.path.join(dirpath, f), root))
    return paths


def detect_language(path: str) -> str | None:
    try:
        lang = tslp.detect_language_from_path(path)
    except Exception:
        return None
    return lang or None


def source_files(root: Path, include_non_code: bool = False) -> list[tuple[str, str]]:
    """Return [(relative_path, language)] for parseable files under root."""
    files = []
    for rel in _list_paths(root):
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        lang = detect_language(rel)
        if not lang or (not include_non_code and lang in NON_CODE):
            continue
        full = root / rel
        try:
            if not full.is_file() or full.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        files.append((rel, lang))
    return sorted(files)
