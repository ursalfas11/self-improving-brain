"""Never lose the thread: a handoff written before the context is compacted, read back right after.

A compaction summary keeps the gist and drops what matters most when you pick up again: the user's
exact words, the question that is still open, the files in flight. Right before Claude Code compacts
(PreCompact hook) the brain writes a handoff straight from the transcript — no LLM, nothing invented —
and injects it again after compaction (SessionStart, source=compact).

`brain resume` prints the latest handoff in any agent, so a Codex session can continue where a Claude
session stopped and the other way round; `brain handoff` writes one by hand (agents without hooks).
Handoffs stay on this machine (`.brain/state/handoff/`): they quote the user verbatim.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import events
from .config import Vault
from .transcripts import Transcript, read_part
from .util import load_json, now_iso, save_json, short

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
TRIVIAL = ("ls", "cat", "pwd", "echo", "cd", "head", "tail", "wc", "which", "true", "clear")
KEEP = 50


def _dir(v: Vault) -> Path:
    return v.path("state", "handoff")


def _safe(session: str) -> str:
    return "".join(c for c in str(session or "") if c.isalnum() or c in "-_")[:80] or "nosession"


def _input(t) -> dict:
    try:
        d = json.loads(t.text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _repo(cwd: str) -> str:
    from .hooks import repo_of
    return repo_of(cwd)


def _git(cwd: str) -> dict:
    if not cwd or not Path(cwd).is_dir():
        return {}
    try:
        br = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3)
        if br.returncode != 0:
            return {}
        st = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=3)
        return {"branch": br.stdout.strip(), "dirty": len([l for l in st.stdout.splitlines() if l.strip()])}
    except (OSError, subprocess.SubprocessError):
        return {}


def build(tr: Transcript, head: Transcript | None = None, cwd: str = "") -> dict:
    """Everything a fresh context needs to continue, taken from the transcript as written."""
    turns = tr.turns
    users = [t for t in turns if t.role == "user"]
    first = (head.user_messages[0].text if head and head.user_messages else (users[0].text if users else ""))
    changed, read, cmds = [], [], []
    for t in turns:
        if t.role != "tool_call":
            continue
        d = _input(t)
        path = str(d.get("file_path") or d.get("notebook_path") or d.get("path") or "")
        if t.tool in WRITE_TOOLS and path:
            if path in changed:
                changed.remove(path)
            changed.append(path)
        elif t.tool == "Read" and path:
            if path in read:
                read.remove(path)
            read.append(path)
        elif t.tool in ("Bash", "shell", "local_shell_call", "exec_command"):
            c = str(d.get("command") or d.get("cmd") or "")
            if isinstance(d.get("command"), list):
                c = " ".join(map(str, d["command"]))
            c = (c.split("<<", 1)[0].splitlines() or [""])[0].strip()
            if c and c.split()[0] not in TRIVIAL:
                if c in cmds:
                    cmds.remove(c)
                cmds.append(c)
    last_answer = next((t.text for t in reversed(turns) if t.role == "assistant"), "")
    last_role = next((t.role for t in reversed(turns) if t.role in ("user", "assistant")), "")
    waiting = last_role == "assistant" and "?" in last_answer[-400:]
    cwd = cwd or tr.cwd
    return {
        "session": tr.session, "agent": tr.source, "cwd": cwd, "repo": _repo(cwd), "t": now_iso(),
        "goal": short(first, 500),
        "user_words": [short(t.text, 700) for t in users[-4:]],
        "changed": changed[-12:], "read": [p for p in read if p not in changed][-6:], "commands": cmds[-8:],
        "last_answer": short(last_answer, 900), "waiting": waiting, "git": _git(cwd),
    }


def render(h: dict, budget: int = 6000) -> str:
    out = [f"Session {h.get('session', '?')[:12]} · {h.get('agent', '?')} · {h.get('repo') or h.get('cwd') or '?'} · {h.get('t', '')[:16]}"]
    g = h.get("git") or {}
    if g:
        out.append(f"git: branch {g.get('branch')} · {g.get('dirty', 0)} uncommitted files")
    if h.get("goal"):
        out.append(f"Started with: “{h['goal']}”")
    if h.get("user_words"):
        out.append("The user's last words (verbatim, newest last):")
        out += [f"  › {w}" for w in h["user_words"]]
    if h.get("waiting"):
        out.append("OPEN: the agent's last message asked the user something and got no answer yet. "
                   "A handoff is not an approval — anything that waited for the user's OK still waits.")
    if h.get("last_answer"):
        out.append(f"Agent's last message: {h['last_answer']}")
    if h.get("changed"):
        out.append("Files changed: " + " · ".join(h["changed"]))
    if h.get("read"):
        out.append("Files read: " + " · ".join(h["read"]))
    if h.get("commands"):
        out.append("Last commands: " + " · ".join(f"`{short(c, 90)}`" for c in h["commands"]))
    if h.get("lessons"):
        out.append("Lessons that applied: " + ", ".join(h["lessons"]))
    text = "\n".join(out)
    return text if len(text) <= budget else text[: budget - 1] + "…"


def save(v: Vault, h: dict) -> Path:
    d = _dir(v)
    st = events.session_state(v, h.get("session", ""))
    h["lessons"] = st.get("lessons", [])[:12]
    p = d / f"{_safe(h.get('session', ''))}.json"
    save_json(p, h)
    idx = [e for e in load_json(d / "index.json", []) if isinstance(e, dict) and e.get("session") != h.get("session")]
    idx.append({k: h.get(k, "") for k in ("session", "agent", "repo", "cwd", "t")})
    for old in idx[:-KEEP]:
        try:
            (d / f"{_safe(old.get('session', ''))}.json").unlink()
        except OSError:
            pass
    save_json(d / "index.json", idx[-KEEP:])
    events.log(v, "handoff", session=h.get("session", ""), repo=h.get("repo", ""))
    return p


def from_transcript(v: Vault, path: Path, cwd: str = "") -> dict | None:
    tail = read_part(path, 3_000_000)
    if tail is None or tail.internal or not tail.turns:
        return None
    head = read_part(path, 300_000, from_end=False)
    h = build(tail, head, cwd)
    save(v, h)
    return h


def load(v: Vault, session: str) -> dict | None:
    h = load_json(_dir(v) / f"{_safe(session)}.json", None)
    return h if isinstance(h, dict) else None


def load_index(v: Vault) -> list[dict]:
    return [e for e in load_json(_dir(v) / "index.json", []) if isinstance(e, dict)]


def latest(v: Vault, repo: str = "", exclude: str = "", max_age_h: float | None = None) -> dict | None:
    import datetime as dt
    for e in reversed(load_index(v)):
        if e.get("session") == exclude:
            continue
        if repo and e.get("repo") != repo:
            continue
        if max_age_h is not None:
            try:
                age = dt.datetime.now() - dt.datetime.fromisoformat(str(e.get("t")))
            except ValueError:
                continue
            if age.total_seconds() > max_age_h * 3600:
                continue
        h = load(v, str(e.get("session", "")))
        if h:
            return h
    return None


def newest_transcript(v: Vault, cwd: str = "") -> Path | None:
    """The most recent session transcript, preferring one that ran in `cwd`."""
    from .learn import transcript_files
    files = transcript_files(v)[-40:]
    if cwd:
        for f in reversed(files):
            tr = read_part(f, 200_000, from_end=False)
            if tr and tr.cwd and Path(tr.cwd).resolve() == Path(cwd).resolve():
                return f
    return files[-1] if files else None
