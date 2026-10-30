"""`brain atlas`: a map of a codebase, kept in the note your agent already gets for that repo.

An agent new to a repo spends its first minutes (and tokens) on `ls`, `cat package.json`, guessing
the test command. The atlas writes that down once, from the repo itself: languages, top-level
layout, entry points and scripts, how to test, CI workflows, and one line per key module. It lives
in `10-projects/<repo>.md` between two markers; everything else in that note stays yours.

The atlas remembers the commit it was made from. When the repo has moved on, the session start
says so (`BRAIN TODO … atlas is 42 commits behind → brain atlas`).
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from .config import Vault
from .notes import Library, parse_frontmatter, set_field
from .util import short, slugify, today

START, END = "<!-- brain:atlas -->", "<!-- /brain:atlas -->"
STALE_COMMITS = 30
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", ".idea", ".vscode",
        "coverage", ".tox", ".mypy_cache", ".pytest_cache", "vendor", ".turbo", ".cache"}
LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
        ".mjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".vue": "Vue", ".svelte": "Svelte",
        ".sh": "Shell", ".sql": "SQL", ".css": "CSS", ".scss": "CSS", ".html": "HTML", ".md": "Markdown"}
SOURCE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".vue", ".svelte"}


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_root(path: Path) -> Path:
    top = _git(path, "rev-parse", "--show-toplevel")
    return Path(top) if top else path.resolve()


def files(root: Path) -> list[str]:
    listed = _git(root, "ls-files")
    if listed:
        return [f for f in listed.splitlines() if not any(p in SKIP for p in f.split("/"))]
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not any(x in SKIP or x.startswith(".") for x in rel.parts[:-1]):
            out.append(rel.as_posix())
        if len(out) > 20000:
            break
    return out


def _first_line(p: Path) -> str:
    """The file's own description: module docstring or the first comment."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[:3000]
    except OSError:
        return ""
    m = re.match(r'\s*(?:#![^\n]*\n)?(?:#[^\n]*coding[^\n]*\n)?\s*(?:"""|\'\'\')\s*([^\n]+)', text)
    if not m:
        m = re.match(r"\s*(?:#![^\n]*\n)?\s*(?:/\*\*?|//|#)\s*([^\n*][^\n]*)", text)
    line = m.group(1).strip().rstrip("*/").strip().removesuffix('"""').removesuffix("'''").strip() if m else ""
    return "" if re.match(r"(?i)^(eslint|@ts-|prettier|type:|noqa|pylint|copyright|license|spdx|use strict)", line) else line


def _scripts(root: Path) -> list[str]:
    out = []
    pj = root / "package.json"
    if pj.is_file():
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
            for k, v in list((d.get("scripts") or {}).items())[:12]:
                out.append(f"`npm run {k}` → `{short(str(v), 70)}`")
            if isinstance(d.get("bin"), dict):
                out += [f"bin `{k}` → `{v}`" for k, v in list(d["bin"].items())[:4]]
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    pp = root / "pyproject.toml"
    if pp.is_file():
        text = pp.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\[project\.scripts\]\s*\n((?:[^\[\n][^\n]*\n?)*)", text, re.M)
        if m:
            for line in m.group(1).splitlines()[:6]:
                if "=" in line:
                    k, v = (x.strip().strip('"') for x in line.split("=", 1))
                    out.append(f"command `{k}` → `{v}`")
    mk = root / "Makefile"
    if mk.is_file():
        targets = re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):(?!=)", mk.read_text(encoding="utf-8", errors="replace"), re.M)
        if targets:
            out.append("make: " + ", ".join(f"`{t}`" for t in list(dict.fromkeys(targets))[:12]))
    jf = root / "justfile"
    if jf.is_file():
        recipes = re.findall(r"^([A-Za-z0-9][A-Za-z0-9_-]*)\s*(?:[^:\n]*):", jf.read_text(encoding="utf-8", errors="replace"), re.M)
        if recipes:
            out.append("just: " + ", ".join(f"`{t}`" for t in recipes[:12]))
    return out


def _test_command(root: Path, fs: list[str]) -> str:
    pj = root / "package.json"
    if pj.is_file():
        try:
            if (json.loads(pj.read_text(encoding="utf-8")).get("scripts") or {}).get("test"):
                runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
                return f"{runner} test"
        except (json.JSONDecodeError, OSError):
            pass
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if any(f.endswith(".py") for f in fs):
        pp = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (root / "pyproject.toml").is_file() else ""
        if "pytest" in pp or (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
            return "pytest"
        if any(re.search(r"(^|/)tests?/test_[^/]+\.py$", f) for f in fs):
            return "python3 -m unittest discover -s tests" if (root / "tests").is_dir() else "python3 -m unittest"
    return ""


def build(path: Path) -> dict:
    root = repo_root(path)
    fs = files(root)
    langs = Counter(LANG[Path(f).suffix] for f in fs if Path(f).suffix in LANG and Path(f).suffix != ".md")
    top = Counter(f.split("/")[0] if "/" in f else "." for f in fs)
    layout = []
    for d, n in sorted(top.items(), key=lambda x: (x[0] == ".", -x[1]))[:14]:
        if d == ".":
            continue
        readme = next((root / d / r for r in ("README.md", "readme.md") if (root / d / r).is_file()), None)
        init = root / d / "__init__.py"
        what = ""
        if readme:
            what = next((l.strip("# ").strip() for l in readme.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.strip() and not l.startswith(("#", "<", "!", "["))), "")
        elif init.is_file():
            what = _first_line(init)
        layout.append((d, n, short(what, 90)))
    src = [f for f in fs if Path(f).suffix in SOURCE and not re.search(r"(^|/)(tests?|spec|__tests__)/|\.test\.|\.spec\.|test_", f)]
    src.sort(key=lambda f: -((root / f).stat().st_size if (root / f).is_file() else 0))
    modules = []
    for f in src[:60]:
        line = _first_line(root / f)
        if line and len(line) > 12:
            modules.append((f, short(line, 110)))
        if len(modules) >= 20:
            break
    ci = sorted(Path(f).stem for f in fs if re.match(r"\.github/workflows/[^/]+\.ya?ml$", f))
    return {"root": str(root), "repo": root.name, "sha": _git(root, "rev-parse", "HEAD"), "date": today(),
            "files": len(fs), "languages": langs.most_common(6), "layout": layout, "scripts": _scripts(root),
            "test": _test_command(root, fs), "ci": ci, "modules": sorted(modules)}


def render(a: dict) -> str:
    langs = ", ".join(f"{l} ({n})" for l, n in a["languages"]) or "–"
    out = [START, f"## Atlas", "", f"_Made by `brain atlas` on {a['date']} from commit `{a['sha'][:10] or '–'}` · "
           f"{a['files']} files · regenerate with `brain atlas`; write your own notes outside this block._", "",
           f"- **Languages:** {langs}"]
    if a["test"]:
        out.append(f"- **Test:** `{a['test']}`")
    if a["ci"]:
        out.append(f"- **CI workflows:** {', '.join(a['ci'])}")
    if a["layout"]:
        out += ["", "### Layout", ""] + [f"- `{d}/` ({n} file{'s' if n != 1 else ''}){' — ' + w if w else ''}" for d, n, w in a["layout"]]
    if a["scripts"]:
        out += ["", "### Entry points and scripts", ""] + [f"- {s}" for s in a["scripts"]]
    if a["modules"]:
        out += ["", "### Key modules", ""] + [f"- `{f}` — {w}" for f, w in a["modules"]]
    out.append(END)
    return "\n".join(out)


def note_for(lib: Library, repo: str):
    from .hooks import repo_notes
    hits = [n for n in repo_notes(lib, repo, k=10) if not n.is_hub]
    return next((n for n in hits if n.meta.get("atlas_sha")), hits[0] if hits else None)


def write(v: Vault, path: Path) -> tuple[Path, dict]:
    a = build(path)
    lib = Library(v.root)
    n = note_for(lib, a["repo"])
    block = render(a)
    if n is None:
        dest = v.root / "10-projects" / f"{slugify(a['repo'])}.md"
        text = (f"---\nname: {slugify(a['repo'])}\ndescription: The {a['repo']} codebase - layout, entry points, how to test\n"
                f"type: project\nrepo: {a['repo']}\natlas_sha: {a['sha']}\nupdated: {today()}\n---\n# {a['repo']}\n\n"
                f"Part of [[hub-projects]].\n\n{block}\n")
    else:
        dest = n.path
        text = dest.read_text(encoding="utf-8")
        if START in text and END in text:
            pre, rest = text.split(START, 1)
            text = pre + block + rest.split(END, 1)[1]
        else:
            text = text.rstrip("\n") + "\n\n" + block + "\n"
        text = set_field(set_field(text, "atlas_sha", a["sha"]), "updated", today())
        if "repo" not in parse_frontmatter(text)[0]:
            text = set_field(text, "repo", a["repo"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest, a


def behind(note, cwd: str) -> int | None:
    """Commits since the atlas was made, or None when there is nothing to compare."""
    sha = str(note.meta.get("atlas_sha") or "")
    if not sha or not cwd:
        return None
    n = _git(Path(cwd), "rev-list", "--count", f"{sha}..HEAD")
    return int(n) if n.isdigit() else None
