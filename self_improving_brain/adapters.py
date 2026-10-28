"""Connect agents to the brain.

  claude   native hooks in ~/.claude/settings.json + slash commands + a block in ~/.claude/CLAUDE.md
  codex    block in ~/.codex/AGENTS.md          (instruction-based: the agent calls the CLI)
  gemini   block in ~/.gemini/GEMINI.md
  agents   block in ./AGENTS.md of the current project (Cursor, Aider, Grok CLI, Zed, Cline, ...)
  print    just print the block

Every file is backed up once before the first change (`*.bak-self-improving-brain`), and every change sits
between markers so `brain uninstall` removes exactly what was added.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .config import Vault

BEGIN, END = "<!-- self-improving-brain:begin -->", "<!-- self-improving-brain:end -->"
HOOK_TAG = "self-improving-brain-hook"
PKG_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"


def _python() -> str:
    """An absolute interpreter that provably runs this code: agents (GUI apps, cron) often have a bare PATH
    whose `python3` is old or missing. Prefer the PATH entry when it is this interpreter (e.g.
    /opt/homebrew/bin/python3): unlike sys.executable it survives interpreter upgrades."""
    found = shutil.which("python3") if os.name != "nt" else None
    try:
        if found and Path(found).resolve() == Path(sys.executable).resolve():
            return f'"{found}"'
    except OSError:
        pass
    return f'"{sys.executable}"'


def console_script() -> Path | None:
    """The `brain` entry point pip, pipx or uv installed for this interpreter (it pins its own venv)."""
    name = "brain.exe" if os.name == "nt" else "brain"
    here = Path(sys.executable).parent / name  # venv, pipx, uv tool
    if here.is_file():
        return here
    import sysconfig  # system-wide or --user install
    dirs = [sysconfig.get_path("scripts")]
    try:
        user = sysconfig.get_preferred_scheme("user") if hasattr(sysconfig, "get_preferred_scheme") else f"{os.name}_user"
        dirs.append(sysconfig.get_path("scripts", user))
    except KeyError:
        pass
    return next((Path(d) / name for d in dirs if d and (Path(d) / name).is_file()), None)


def brain_cmd() -> str:
    """Absolute command, so hooks work no matter what PATH the agent runs with."""
    shim = PKG_ROOT / "brain"
    if shim.is_file():  # running from a clone
        return f'{_python()} "{shim}"'
    script = console_script()
    if script:
        return f'"{script}"'
    # -P (3.11+) keeps the agent's working directory off sys.path, so a project file can't shadow a module
    return f'"{sys.executable}"{" -P" if sys.version_info >= (3, 11) else ""} -m self_improving_brain'


def cli_name() -> str:
    """`brain` if it is on PATH and points here, else the full command."""
    found = shutil.which("brain")
    if found:
        shim = PKG_ROOT / "brain"
        ours = shim if shim.is_file() else console_script()
        if ours and Path(found).resolve() == ours.resolve():
            return "brain"
    return brain_cmd()


def block(v: Vault) -> str:
    b = brain_cmd()
    return f"""{BEGIN}
## Brain (self-improving-brain)
You have a persistent, self-improving memory at `{v.root}`. Use it — it is how you get smarter over time.
- **Start of a task:** run `{b} context` once (skip if a BRAIN block is already in your context).
- **Before answering from memory** (project facts, decisions, "how did we…"): `{b} search "<question>"`, then `{b} read <note> [section]`. Cite the note.
- **Learned something worth keeping** (a decision, a pitfall, a user preference, a fact that took effort): `{b} remember <fact|decision|pitfall|preference|todo|status> "<one self-contained sentence>" --quote "<user's words>"`. Never store secrets — store the variable name and where the value lives.
- **Lessons** (`LESSON …` lines) are rules learned from your past mistakes with this user. Follow them; they outrank your defaults.
- **Never** approve lesson drafts or delete notes on your own; show the user and wait for an explicit OK.
{END}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.sib.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak-self-improving-brain")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def _put_block(path: Path, text: str) -> str:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    _backup(path)
    if (BEGIN in old) != (END in old):
        raise SystemExit(f"{path} has a broken self-improving-brain block (one marker missing) — fix it by hand first")
    if BEGIN in old and END in old:
        pre, rest = old.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        new = pre + text.strip() + post
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + text.strip() + "\n"
    _write(path, new)
    return f"updated {path}"


def _drop_block(path: Path) -> str:
    if not path.exists():
        return f"skip {path} (missing)"
    old = path.read_text(encoding="utf-8")
    if BEGIN not in old:
        return f"skip {path} (no self-improving-brain block)"
    if END not in old:
        return f"skip {path} (self-improving-brain end marker missing — remove the block by hand)"
    pre, rest = old.split(BEGIN, 1)
    _, post = rest.split(END, 1)
    _write(path, (pre.rstrip() + "\n" + post.lstrip()).strip() + "\n")
    return f"cleaned {path}"


def _ours(group) -> bool:
    return isinstance(group, dict) and any(
        isinstance(h, dict) and HOOK_TAG in str(h.get("command", "")) for h in group.get("hooks", []) or [])


CLAUDE_HOOKS = [
    ("SessionStart", None, "session-start", 15, False),
    ("UserPromptSubmit", None, "prompt", 10, False),
    ("PreToolUse", "*", "pre-tool", 10, False),
    ("PostToolUse", "Read|Bash|Write|Edit|MultiEdit", "post-tool", 15, False),
    ("Stop", None, "stop", 10, False),
    ("SessionEnd", None, "session-end", 5, True),
]


def _claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_settings(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{p} is not valid JSON ({e}); nothing changed. Fix it, then rerun.")
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise SystemExit(f"{p} has an unexpected shape; nothing changed.")
    return data


def install_claude(v: Vault) -> list[str]:
    p = _claude_settings()
    data = _load_settings(p)
    _backup(p)
    hooks = data.setdefault("hooks", {})
    for event, matcher, handler, timeout, is_async in CLAUDE_HOOKS:
        groups = [g for g in hooks.get(event, []) if not _ours(g)]
        h = {"type": "command", "command": f"{brain_cmd()} hook {handler}  # {HOOK_TAG}", "timeout": timeout}
        if is_async:
            h["async"] = True
        g = {"hooks": [h]}
        if matcher:
            g["matcher"] = matcher
        groups.append(g)
        hooks[event] = groups
    _write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    out = [f"hooks → {p}"]
    cmd_dir = Path.home() / ".claude" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for t in sorted((TEMPLATES / "commands").glob("*.md")):
        (cmd_dir / t.name).write_text(t.read_text(encoding="utf-8").replace("{{BRAIN}}", brain_cmd()), encoding="utf-8")
        out.append(f"command /{t.stem}")
    out.append(_put_block(Path.home() / ".claude" / "CLAUDE.md", block(v)))
    return out


def uninstall_claude() -> list[str]:
    out = []
    p = _claude_settings()
    if p.exists():
        data = _load_settings(p)
        hooks = data.get("hooks", {})
        for event in list(hooks):
            if not isinstance(hooks[event], list):
                continue
            hooks[event] = [g for g in hooks[event] if not _ours(g)]
            if not hooks[event]:
                del hooks[event]
        _write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        out.append(f"hooks removed from {p}")
    for t in (TEMPLATES / "commands").glob("*.md"):
        f = Path.home() / ".claude" / "commands" / t.name
        if f.exists():
            f.unlink()
            out.append(f"removed /{t.stem}")
    out.append(_drop_block(Path.home() / ".claude" / "CLAUDE.md"))
    return out


TARGETS = {
    "codex": lambda: Path.home() / ".codex" / "AGENTS.md",
    "gemini": lambda: Path.home() / ".gemini" / "GEMINI.md",
    "agents": lambda: Path.cwd() / "AGENTS.md",
}


def install(v: Vault, agent: str) -> list[str]:
    if agent == "claude":
        return install_claude(v)
    if agent == "print":
        return [block(v)]
    if agent in TARGETS:
        return [_put_block(TARGETS[agent](), block(v))]
    raise ValueError(f"unknown agent {agent!r}; choose claude, codex, gemini, agents or print")


def uninstall(agent: str) -> list[str]:
    if agent == "claude":
        return uninstall_claude()
    if agent in TARGETS:
        return [_drop_block(TARGETS[agent]())]
    raise ValueError(f"unknown agent {agent!r}")
