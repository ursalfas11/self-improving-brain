"""Trusted lessons: a rule only reaches your agent if you put it there.

A lesson is plain text in a note, so anything that can write a file could plant one — including an
agent that read a poisoned web page. The ledger (`.brain/trust.json`, versioned with the vault) holds a
fingerprint of every lesson you approved; hooks inject a lesson only while its text still matches.

trust_mode
  agent   (default) lessons you write or edit yourself (Obsidian, an editor) are trusted on sight.
          A lesson that appears or changes during an agent's tool call stays silent until `brain trust`.
  strict  only lessons created through the brain (approve, lesson, pack, team) or `brain trust` count.
  off     every lesson counts.

Team lessons (`brain team`) are other people's input: in agent and strict mode they count only as
they were when you accepted them in `brain team add` / `brain team sync`.
"""
from __future__ import annotations

from .config import Vault
from .notes import Lesson
from .util import digest, load_json, now_iso, save_json

MODES = ("agent", "strict", "off")


def fingerprint(l: Lesson) -> str:
    return digest(l.id, l.rule, l.mechanism, ",".join(l.tools), l.commands, l.paths, ",".join(l.words), l.check)


def _path(v: Vault):
    return v.path("trust.json")


def load(v: Vault) -> dict:
    data = load_json(_path(v), None)
    if not isinstance(data, dict):
        return {}
    for k in ("trusted", "pending"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def _save(v: Vault, data: dict) -> None:
    save_json(_path(v), data)


def record(v: Vault, lessons: list[Lesson], by: str) -> None:
    """Trust these lessons as they are now (approve, lesson, pack, team, brain trust)."""
    data = load(v) or {"trusted": {}, "pending": {}}
    for l in lessons:
        data["trusted"][l.id] = {"h": fingerprint(l), "by": by, "t": now_iso()}
        data["pending"].pop(l.id, None)
    _save(v, data)


def _team(l: Lesson) -> bool:
    return l.note.startswith("team:")


def mark_pending(v: Vault, lessons: list[Lesson], session: str, tool: str) -> list[str]:
    """An agent's tool call just created or changed these lessons: keep them silent until you look."""
    data = load(v)
    if not data:
        return []
    out = []
    for l in lessons:
        if _team(l):
            continue  # silent anyway until `brain team sync`
        fp = fingerprint(l)
        if data["trusted"].get(l.id, {}).get("h") == fp or data["pending"].get(l.id, {}).get("h") == fp:
            continue
        data["pending"][l.id] = {"h": fp, "t": now_iso(), "session": session, "tool": tool, "note": l.note}
        out.append(l.id)
    if out:
        _save(v, data)
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
        elif data["pending"].get(l.id, {}).get("h") == fp or mode == "strict" or _team(l):
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
        if _team(l):
            out.append((l, f"from team {l.note.split(':')[1]}, not accepted in this form → brain team sync"))
        elif p and p.get("h") == fp:
            out.append((l, f"written by an agent ({p.get('tool', '?')}, {str(p.get('t', ''))[:16]})"))
        elif mode == "strict":
            out.append((l, "changed since you approved it" if l.id in data["trusted"] else "never approved"))
    return out
