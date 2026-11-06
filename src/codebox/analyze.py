"""Direct (index-free) analyses: outline, structural search, grep, complexity, hotspots, semgrep."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import ast_grep_py as sg
import lizard

from .discover import detect_language, resolve_root, source_files
from .parse import parse_file

# tree-sitter-language-pack name -> ast-grep-py name
AST_GREP_LANGS = {
    "python": "python", "javascript": "javascript", "typescript": "typescript", "tsx": "tsx",
    "go": "go", "rust": "rust", "java": "java", "c": "c", "cpp": "cpp", "c_sharp": "csharp",
    "csharp": "csharp", "ruby": "ruby", "kotlin": "kotlin", "swift": "swift", "scala": "scala",
    "lua": "lua", "php": "php", "bash": "bash", "html": "html", "css": "css", "elixir": "elixir",
    "haskell": "haskell", "yaml": "yaml", "json": "json", "solidity": "solidity", "nix": "nix",
}


def _snippet(text: str, n: int = 240) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


def _within(rel: str, path: str | None) -> bool:
    return not path or rel == path or rel.startswith(path.rstrip("/") + "/")


# --------------------------------------------------------------------------- outline

def outline(file: str, root=None) -> dict:
    root = resolve_root(root)
    p = (root / file) if not Path(file).is_absolute() else Path(file)
    lang = detect_language(str(p))
    if not lang:
        raise ValueError(f"Unknown language for {file}")
    f = parse_file(str(p.relative_to(root) if p.is_relative_to(root) else p), lang, p.read_bytes())
    return {
        "path": f.path, "language": lang, "lines": f.lines, "parse_errors": f.errors,
        "imports": [{"line": i.line, "source": i.source} for i in f.imports],
        "symbols": [{"qualname": s.qualname, "kind": s.kind, "line": s.start_line, "end_line": s.end_line,
                     "signature": s.signature} for s in f.symbols],
        "notes": f.notes,
    }


# --------------------------------------------------------------------------- ast-grep

def structural_search(pattern: str | None, language: str, root=None, path: str | None = None,
                      rule: dict | None = None, limit: int = 100) -> dict:
    """ast-grep search. `pattern` like `requests.get($URL, $$$)`; or a full ast-grep `rule` dict."""
    if not pattern and not rule:
        raise ValueError("Provide pattern or rule")
    root = resolve_root(root)
    want = AST_GREP_LANGS.get(language, language)
    try:  # validate once so a bad pattern gives one clear error
        probe = sg.SgRoot("", want).root()
        probe.find_all(config={"rule": rule}) if rule else probe.find_all(pattern=pattern)
    except Exception as e:
        raise ValueError(f"invalid ast-grep {'rule' if rule else 'pattern'} for {want}: {e}") from None
    results, scanned = [], 0
    for rel, lang in source_files(root, include_non_code=True):
        if AST_GREP_LANGS.get(lang) != want or not _within(rel, path):
            continue
        scanned += 1
        try:
            node = sg.SgRoot((root / rel).read_text(errors="replace"), want).root()
            matches = node.find_all(config={"rule": rule}) if rule else node.find_all(pattern=pattern)
        except Exception:
            continue  # unreadable/unparseable file; pattern already validated
        for m in matches:
            r = m.range()
            results.append({"path": rel, "line": r.start.line + 1, "end_line": r.end.line + 1,
                            "match": _snippet(m.text())})
            if len(results) >= limit:
                return {"files_scanned": scanned, "matches": results, "truncated": True}
    return {"files_scanned": scanned, "matches": results, "truncated": False}


# --------------------------------------------------------------------------- ripgrep

def grep(regex: str, root=None, path: str | None = None, glob: str | None = None,
         ignore_case: bool = False, limit: int = 100) -> dict:
    root = resolve_root(root)
    if not shutil.which("rg"):
        raise RuntimeError("ripgrep (rg) not found on PATH")
    cmd = ["rg", "--json", "-m", str(limit)]
    if ignore_case:
        cmd.append("-i")
    if glob:
        cmd += ["-g", glob]
    cmd += ["-e", regex, str(root / path) if path else str(root)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    results = []
    for line in out.stdout.splitlines():
        ev = json.loads(line)
        if ev.get("type") != "match":
            continue
        d = ev["data"]
        p = d["path"].get("text", "")
        results.append({"path": str(Path(p).relative_to(root)) if Path(p).is_absolute() else p,
                        "line": d["line_number"], "text": _snippet(d["lines"].get("text", ""))})
        if len(results) >= limit:
            break
    return {"matches": results, "truncated": len(results) >= limit}


# --------------------------------------------------------------------------- lizard

def complexity(root=None, path: str | None = None, min_ccn: int = 10, limit: int = 50) -> dict:
    """Cyclomatic complexity per function (lizard). Returns functions with CCN >= min_ccn."""
    root = resolve_root(root)
    funcs, per_file = [], {}
    for rel, _ in source_files(root):
        if not _within(rel, path):
            continue
        try:
            info = lizard.analyze_file.analyze_source_code(rel, (root / rel).read_text(errors="replace"))
        except Exception:
            continue
        if not info.function_list:
            continue
        per_file[rel] = sum(f.cyclomatic_complexity for f in info.function_list)
        for fn in info.function_list:
            if fn.cyclomatic_complexity >= min_ccn:
                funcs.append({"path": rel, "function": fn.name, "line": fn.start_line, "ccn": fn.cyclomatic_complexity,
                              "nloc": fn.nloc, "params": len(fn.parameters)})
    funcs.sort(key=lambda x: -x["ccn"])
    return {"functions": funcs[:limit], "total_over_threshold": len(funcs), "file_ccn": per_file}


# --------------------------------------------------------------------------- git hotspots

def hotspots(root=None, days: int = 180, limit: int = 25) -> dict:
    """Churn x complexity: files that change often AND are complex are where bugs live."""
    root = resolve_root(root)
    if not (root / ".git").exists():
        return {"error": "not a git repository", "hotspots": []}
    out = subprocess.run(["git", "-C", str(root), "log", f"--since={days} days ago", "--numstat", "--format=%H"],
                         capture_output=True, text=True, timeout=120)
    commits, churn = defaultdict(int), defaultdict(int)
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d, p = parts
            commits[p] += 1
            churn[p] += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    ccn = complexity(str(root), min_ccn=10**9)["file_ccn"]
    rows = [{"path": p, "commits": commits[p], "churn": churn[p], "ccn": ccn[p], "score": commits[p] * ccn[p]}
            for p in commits if p in ccn]
    rows.sort(key=lambda r: -r["score"])
    return {"window_days": days, "hotspots": rows[:limit]}


# --------------------------------------------------------------------------- semgrep

def semgrep_scan(root=None, path: str | None = None, config: str = "p/default",
                 limit: int = 100, timeout: int = 600) -> dict:
    """Run semgrep (installed, or via `uvx semgrep`). `config` may be a registry pack or a local rules path."""
    root = resolve_root(root)
    if shutil.which("semgrep"):
        base = ["semgrep"]
    elif shutil.which("uvx"):
        base = ["uvx", "semgrep"]
    else:
        raise RuntimeError("semgrep not found; install it or install uv so `uvx semgrep` works")
    target = str(root / path) if path else str(root)
    cmd = base + ["scan", "--json", "--quiet", "--metrics=off", "--config", config, target]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=root)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"error": (out.stderr or out.stdout)[-2000:]}
    def code_at(path: str, start: int, end: int) -> str:
        try:  # semgrep redacts `lines` without a login; read the evidence ourselves
            lines = (root / path).read_text(errors="replace").splitlines()
            return _snippet("\n".join(lines[start - 1:min(end, start + 4)]), 300)
        except OSError:
            return ""

    findings = []
    for r in data.get("results", []):
        rel = str(Path(r["path"]).relative_to(root)) if Path(r["path"]).is_absolute() else r["path"]
        findings.append({
            "rule": r["check_id"], "severity": r["extra"].get("severity"), "path": rel,
            "line": r["start"]["line"], "message": _snippet(r["extra"].get("message", ""), 400),
            "code": code_at(rel, r["start"]["line"], r["end"]["line"]),
        })
    sev = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    findings.sort(key=lambda f: sev.get(f["severity"], 3))
    return {"config": config, "total": len(findings), "findings": findings[:limit],
            "errors": [e.get("message", "")[:300] for e in data.get("errors", [])][:10]}
