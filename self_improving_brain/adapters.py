"""Connect agents to the brain.

  claude   native hooks in ~/.claude/settings.json + the /brain command + a block in ~/.claude/CLAUDE.md
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


def brain_argv() -> list[str]:
    """brain_cmd() as an argument list, for configs that take command + args (MCP clients)."""
    shim = PKG_ROOT / "brain"
    if shim.is_file():
        return [_python().strip('"'), str(shim)]
    script = console_script()
    if script:
        return [str(script)]
    return [sys.executable, *(["-P"] if sys.version_info >= (3, 11) else []), "-m", "self_improving_brain"]


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
    """For agents without hooks (Codex, Gemini CLI, AGENTS.md). It sits in every session, so every word counts:
    the long command is named once."""
    return f"""{BEGIN}
## Brain (self-improving-brain)
You have a persistent memory at `{v.root}` that learns from this user's corrections. Below, `brain` stands for `{brain_cmd()}`.
- **Start of a task:** run `brain context` once (skip it if a SELF-IMPROVING-BRAIN line is already in your context).
- **Before answering from memory** (project facts, decisions, "how did we…"): `brain search "<question>"`, then `brain read <note> [section]`.
- **Worth keeping** (a decision, a pitfall, a preference, a fact that took effort): `brain remember <fact|decision|pitfall|preference> "<one sentence>"`. Never store secrets: store the variable name and where the value lives.
- **LESSON lines** are rules learned from this user's corrections. Follow them; they outrank your defaults.
- **Before an irreversible action** (force push, deleting data, deploys, migrations): `brain check --command "<command>"`. If it names a GUARD, ask the user first.
- **Continuing work** ("where were we", after a break or in another agent): `brain resume`. Before you end a long task: `brain handoff`.
- Which rules reach you is the user's call: never run `brain trust`, `brain undo` or `brain approve` on your own.
{END}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.sib.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> None:
    """The file as it was before the brain's first edit. A file the brain already edited is not that."""
    bak = path.with_name(path.name + ".bak-self-improving-brain")
    if path.exists() and not bak.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if BEGIN not in text and HOOK_TAG not in text:
            shutil.copy2(path, bak)


def _tidy(path: Path) -> bool:
    """After the brain's part is out: a file that held only that goes, and so does the backup once the file equals
    it again. True when the file is gone."""
    bak = path.with_name(path.name + ".bak-self-improving-brain")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if text.strip() in ("", "{}"):
        path.unlink()
        text = ""
    try:
        old = bak.read_text(encoding="utf-8")
        try:
            same = json.loads(old or "{}") == json.loads(text or "{}")
        except ValueError:
            same = old.split() == text.split()
        if same:
            bak.unlink()
    except OSError:
        pass
    return not text


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


def has_block(path: Path) -> bool:
    try:
        return BEGIN in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _drop_block(path: Path) -> str:
    if not path.exists():
        return ""
    old = path.read_text(encoding="utf-8")
    if BEGIN not in old:
        return ""  # nothing of the brain's in it: nothing to say
    if END not in old:
        return f"skip {path} (self-improving-brain end marker missing — remove the block by hand)"
    pre, rest = old.split(BEGIN, 1)
    _, post = rest.split(END, 1)
    _write(path, (pre.rstrip() + "\n" + post.lstrip()).strip() + "\n")
    return f"removed {path} (it held only the brain's part)" if _tidy(path) else f"took the brain's part out of {path}"


def _ours(group) -> bool:
    return isinstance(group, dict) and any(
        isinstance(h, dict) and HOOK_TAG in str(h.get("command", "")) for h in group.get("hooks", []) or [])


CLAUDE_HOOKS = [
    ("SessionStart", None, "session-start", 15, False),
    ("UserPromptSubmit", None, "prompt", 10, False),
    ("PreToolUse", "*", "pre-tool", 10, False),
    ("PostToolUse", "Read|Bash|Write|Edit|MultiEdit|NotebookEdit", "post-tool", 15, False),
    ("PreCompact", None, "pre-compact", 20, False),
    ("SessionEnd", None, "session-end", 5, True),
]


def from_plugin() -> bool:
    """Running from a Claude Code plugin install (its path changes with every plugin update)."""
    return "/.claude/plugins/" in PKG_ROOT.as_posix() + "/"


def plugin_hooks() -> dict:
    """hooks/hooks.json of the Claude Code plugin; tests keep the file in sync with CLAUDE_HOOKS."""
    out: dict = {}
    for event, matcher, handler, timeout, is_async in CLAUDE_HOOKS:
        h = {"type": "command", "command": f'python3 "${{CLAUDE_PLUGIN_ROOT}}/brain" hook {handler} --plugin', "timeout": timeout}
        if is_async:
            h["async"] = True
        g = {"matcher": matcher, "hooks": [h]} if matcher else {"hooks": [h]}
        out.setdefault(event, []).append(g)
    return {"description": "self-improving-brain: rules from your corrections at the right moment, guards, recall, handoffs",
            "hooks": out}


def plugin_command(t: Path) -> str:
    """A slash command for the plugin: `brain` is the command from the SELF-IMPROVING-BRAIN context line."""
    _, head, body = t.read_text(encoding="utf-8").split("---\n", 2)
    return (f"---\n{head}---\n`brain` below is the brain command shown in the SELF-IMPROVING-BRAIN line of your context.\n\n"
            + body.lstrip("\n").replace("{{BRAIN}}", "brain"))


def claude_hooks_installed() -> bool:
    try:
        return HOOK_TAG in _claude_settings().read_text(encoding="utf-8")
    except OSError:
        return False


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


# slash commands of earlier versions, removed on install and uninstall
OLD_COMMANDS = ("brain-review.md", "brain-distill.md", "brain-ask.md", "brain-ingest.md")


def _ours_command(f: Path) -> bool:
    """A command file this tool wrote (its description names the brain), not one of yours with the same name."""
    try:
        head = f.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        return False
    return any(l.startswith("description:") and "brain" in l.lower() for l in head.splitlines())


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
    for old in OLD_COMMANDS:
        if _ours_command(cmd_dir / old):
            (cmd_dir / old).unlink()
    for t in sorted((TEMPLATES / "commands").glob("*.md")):
        (cmd_dir / t.name).write_text(t.read_text(encoding="utf-8").replace("{{BRAIN}}", brain_cmd()), encoding="utf-8")
        out.append(f"command /{t.stem}")
    # the hooks say all of it, and only where it matters: a block in CLAUDE.md would sit in every session
    gone = _drop_block(Path.home() / ".claude" / "CLAUDE.md")
    if gone:
        out.append(gone)
    return out


def uninstall_claude() -> list[str]:
    out = []
    p = _claude_settings()
    if p.exists():
        data = _load_settings(p)
        hooks = data.get("hooks", {})
        removed = 0
        for event in list(hooks):
            if not isinstance(hooks[event], list):
                continue
            keep = [g for g in hooks[event] if not _ours(g)]
            removed += len(hooks[event]) - len(keep)
            hooks[event] = keep
            if not hooks[event]:
                del hooks[event]
        if removed:
            if isinstance(hooks, dict) and not hooks:
                data.pop("hooks", None)
            _write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            out.append(f"removed the hooks from {p}" + (" (and the file, it held only them)" if _tidy(p) else ""))
    for name in [t.name for t in (TEMPLATES / "commands").glob("*.md")] + list(OLD_COMMANDS):
        f = Path.home() / ".claude" / "commands" / name
        if _ours_command(f):
            f.unlink()
            out.append(f"removed the /{f.stem} command")
    out.append(_drop_block(Path.home() / ".claude" / "CLAUDE.md"))
    return [x for x in out if x]


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
        return [x for x in [_drop_block(TARGETS[agent]())] if x]
    raise ValueError(f"unknown agent {agent!r}")
