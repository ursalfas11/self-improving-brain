"""Trusted lessons: a rule only reaches your agent if you put it there.

A lesson is plain text in a note, so anything that can write a file could plant one — including an
agent that read a poisoned web page. The ledger (`.brain/trust/<host>.json`, versioned with the vault, one
file per machine) holds a fingerprint of every lesson you approved; hooks inject a lesson only while its text
still matches. Reading merges the machines' files: per lesson, the latest entry counts.

trust_mode
  agent   (default) lessons you write or edit yourself (Obsidian, an editor) are trusted on sight.
          A lesson that appears or changes during an agent's tool call stays silent until `brain trust`.
  strict  only lessons created through the brain (learned at night, approve, lesson) or `brain trust` count.
  off     every lesson counts.
"""
from __future__ import annotations

from .config import Vault
from .notes import Lesson
from .util import digest, host, load_json, now_iso, save_json

MODES = ("agent", "strict", "off")


def fingerprint(l: Lesson) -> str:
    return digest(l.id, l.rule, l.mechanism, ",".join(l.tools), l.commands, l.paths, ",".join(l.words), l.check)


def _path(v: Vault):
    """This machine's ledger: the only one it writes."""
    p = v.path("trust", f"{host()}.json")
    old = v.path("trust.json")
    if old.exists():  # until 0.2 all machines shared one file: it becomes this machine's
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            old.replace(p)
        else:
            old.unlink()
    return p


def _read(f) -> dict:
    data = load_json(f, None)
    if not isinstance(data, dict):
        return {}
    for k in ("trusted", "pending"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def load(v: Vault) -> dict:
    """Every machine's ledger in one: per lesson the latest entry. {} while there is none at all."""
    own = _path(v)
    files = [own] + sorted(p for p in own.parent.glob("*.json") if p != own) if own.parent.exists() else []
    out: dict = {}
    for f in files:
        data = _read(f)
        if not data:
            continue
        out = out or {"trusted": {}, "pending": {}}
        for k in ("trusted", "pending"):
            for lid, e in data[k].items():
                cur = out[k].get(lid)
                if isinstance(e, dict) and (cur is None or str(e.get("t", "")) > str(cur.get("t", ""))):
                    out[k][lid] = e
    return out


def _save(v: Vault, changes: dict) -> None:
    """Merge these entries into this machine's file (other machines' files are never written here)."""
    p = _path(v)
    data = _read(p) or {"trusted": {}, "pending": {}}
    for k in ("trusted", "pending"):
        for lid, e in changes.get(k, {}).items():
            if e is None:
                data[k].pop(lid, None)
            else:
                data[k][lid] = e
    save_json(p, data)


def record(v: Vault, lessons: list[Lesson], by: str) -> None:
    """Trust these lessons as they are now (learned at night, approve, lesson, brain trust)."""
    data: dict = {"trusted": {}, "pending": {}}
    for l in lessons:
        data["trusted"][l.id] = {"h": fingerprint(l), "by": by, "t": now_iso()}
        data["pending"][l.id] = None
    _save(v, data)


def mark_pending(v: Vault, lessons: list[Lesson], session: str, tool: str) -> list[str]:
    """An agent's tool call just created or changed these lessons: keep them silent until you look."""
    data = load(v)
    if not data:
        return []
    out, new = [], {}
    for l in lessons:
        fp = fingerprint(l)
        if data["trusted"].get(l.id, {}).get("h") == fp or data["pending"].get(l.id, {}).get("h") == fp:
            continue
        new[l.id] = {"h": fp, "t": now_iso(), "session": session, "tool": tool, "note": l.note}
        out.append(l.id)
    if out:
        _save(v, {"pending": new})
    return out


def effective(v: Vault, lessons: list[Lesson]) -> list[Lesson]:
    """The lessons hooks may inject. Bootstraps the ledger on first use (an existing vault keeps working)."""
    mode = str(v.get("trust_mode") or "agent")
    if mode == "off":
        return lessons
    data = load(v)
    if not data:
        record(v, lessons, "bootstrap")
        return lessons
    out, seen = [], []
    for l in lessons:
        fp = fingerprint(l)
        if data["trusted"].get(l.id, {}).get("h") == fp:
            out.append(l)
        elif data["pending"].get(l.id, {}).get("h") == fp or mode == "strict":
            continue
        else:
            seen.append(l)  # written by you outside any agent tool call
            out.append(l)
    if seen:
        record(v, seen, "seen")
    return out


def untrusted(v: Vault, lessons: list[Lesson]) -> list[tuple[Lesson, str]]:
    """(lesson, why) for every lesson that is currently silent."""
    mode = str(v.get("trust_mode") or "agent")
    if mode == "off":
        return []
    data = load(v)
    if not data:
        return []
    out = []
    for l in lessons:
        fp = fingerprint(l)
        if data["trusted"].get(l.id, {}).get("h") == fp:
            continue
        p = data["pending"].get(l.id)
        if p and p.get("h") == fp:
            out.append((l, f"written by an agent ({p.get('tool', '?')}, {str(p.get('t', ''))[:16]})"))
        elif mode == "strict":
            out.append((l, "changed since you approved it" if l.id in data["trusted"] else "never approved"))
    return out
