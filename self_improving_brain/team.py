"""Team brain: lessons your whole team learned, shared through a plain git repo.

  brain team add acme git@github.com:acme/agent-brain.git   clone it, show its lessons, trust them on your OK
  brain team sync                                           pull; new or changed lessons wait for your OK
  brain team propose L-fix-flaky-tests-root acme            offer one of your lessons on a branch
  brain team list · brain team remove acme

The clone lives in `.brain/team/<name>/` (not versioned with your vault). Its lessons work like your
own, but only as they were when you said yes: anything the team changes later stays silent until
`brain team sync` showed it to you. A shared repo is input from other people, not from you.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import trust
from .config import Vault
from .notes import Lesson, iter_note_paths, load_note
from .util import host, load_json, now_iso, save_json, slugify

PREFIX = "team:"


def _dir(v: Vault, name: str = "") -> Path:
    return v.path("team", name) if name else v.path("team")


def _ledger(v: Vault) -> dict:
    d = load_json(v.path("team.json"), {})
    return d if isinstance(d, dict) else {}


def _git(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)


def names(v: Vault) -> list[str]:
    d = _dir(v)
    return sorted(p.name for p in d.iterdir() if (p / ".git").exists()) if d.is_dir() else []


def lessons(v: Vault, only: str = "") -> list[Lesson]:
    """Every lesson in the team clones; their note is `team:<name>:<note>`."""
    out = []
    for name in names(v):
        if only and name != only:
            continue
        root = _dir(v, name)
        for p in iter_note_paths(root):
            n = load_note(p, root)
            if n:
                for l in n.lessons:
                    l.note = f"{PREFIX}{name}:{n.name}"
                    out.append(l)
    return out


def fingerprint_paths(v: Vault):
    for name in names(v):
        yield from iter_note_paths(_dir(v, name))


def _ensure_ignored(v: Vault) -> None:
    gi = v.root / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".brain/team/" not in text:
        gi.write_text(text.rstrip("\n") + ("\n" if text else "") + "# team brains are their own git repos\n.brain/team/\n",
                      encoding="utf-8")


def pending(v: Vault, name: str) -> list[tuple[Lesson, str]]:
    """Team lessons you have not said yes to in their current form: (lesson, 'new' | 'changed')."""
    data = trust.load(v).get("trusted", {})
    out = []
    for l in lessons(v, name):
        rec = data.get(l.id)
        if rec and rec.get("h") == trust.fingerprint(l):
            continue
        out.append((l, "changed" if rec else "new"))
    return out


def clone(v: Vault, name: str, url: str) -> list[tuple[Lesson, str]]:
    name = slugify(name)
    dest = _dir(v, name)
    if dest.exists():
        raise ValueError(f"team {name} already exists — `brain team sync {name}`")
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "-q", "--depth", "50", url, str(dest)], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise ValueError(f"git clone failed: {r.stderr.strip()[:300]}")
    _ensure_ignored(v)
    led = _ledger(v)
    led[name] = {"url": url, "added": now_iso()}
    save_json(v.path("team.json"), led)
    return pending(v, name)


def accept(v: Vault, name: str, ls: list[Lesson]) -> None:
    trust.record(v, ls, PREFIX + name)
    led = _ledger(v)
    led.setdefault(name, {})["sha"] = _git(_dir(v, name), "rev-parse", "HEAD").stdout.strip()
    led[name]["synced"] = now_iso()
    save_json(v.path("team.json"), led)


def pull(v: Vault, name: str) -> list[tuple[Lesson, str]]:
    dest = _dir(v, name)
    if not (dest / ".git").exists():
        raise ValueError(f"no team {name} — `brain team add {name} <git-url>`")
    r = _git(dest, "pull", "-q", "--ff-only", timeout=180)
    if r.returncode != 0:
        raise ValueError(f"git pull failed in {dest}: {r.stderr.strip()[:300]}")
    return pending(v, name)


def remove(v: Vault, name: str) -> Path:
    dest = _dir(v, name)
    if not dest.exists():
        raise ValueError(f"no team {name}")
    shutil.rmtree(dest)
    led = _ledger(v)
    led.pop(name, None)
    save_json(v.path("team.json"), led)
    return dest


def propose(v: Vault, lesson: Lesson, name: str, push: bool = True) -> dict:
    """Your lesson on a branch of the team repo, without evidence or dates; pushed so you can open a PR."""
    from . import secrets
    dest = _dir(v, name)
    if not (dest / ".git").exists():
        raise ValueError(f"no team {name}")
    c = Lesson(id=lesson.id, note="", rule=lesson.rule, mechanism=lesson.mechanism, tools=lesson.tools,
               commands=lesson.commands, paths=lesson.paths, words=lesson.words, check=lesson.check)
    if secrets.scan(c.as_callout()):
        raise ValueError("this lesson looks like it contains a secret — not proposed")
    branch = f"propose/{slugify(lesson.id)}-{slugify(host())[:20]}"
    base = _git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    r = _git(dest, "switch", "-q", "-c", branch)
    if r.returncode != 0:
        raise ValueError(f"git switch failed: {r.stderr.strip()[:200]}")
    try:
        f = dest / "proposed" / f"{slugify(lesson.id)}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"---\nname: proposed-{slugify(lesson.id)}\ndescription: Proposed lesson - {lesson.rule[:150]}\n"
                     f"type: resource\n---\n# Proposed: {lesson.id}\n\nMove it into the note that owns the topic when you "
                     f"merge.\n\n{c.as_callout()}\n", encoding="utf-8")
        _git(dest, "add", str(f.relative_to(dest)))
        r = _git(dest, "-c", "user.name=self-improving-brain", "-c", "user.email=brain@localhost",
                 "commit", "-q", "-m", f"Propose lesson {lesson.id}")
        if r.returncode != 0:
            raise ValueError(f"git commit failed: {(r.stderr or r.stdout).strip()[:200]}")
        out = {"branch": branch, "file": f, "pushed": False}
        if push:
            p = _git(dest, "push", "-q", "-u", "origin", branch, timeout=120)
            out["pushed"] = p.returncode == 0
            out["push_error"] = p.stderr.strip()[:200] if p.returncode else ""
        url = _ledger(v).get(name, {}).get("url", "")
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        out["pr"] = f"gh pr create -R {m.group(1)} --head {branch} --title 'Lesson: {lesson.id}'" if m else ""
        return out
    finally:
        _git(dest, "switch", "-q", base)
