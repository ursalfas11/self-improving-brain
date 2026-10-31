"""What the brain changed on its own, and how to take it back.

Every change the nightly run makes without asking is one line in `.brain/journal/<host>.jsonl`, with what it
needs to be undone (append-only and one file per machine, so syncing the vault never conflicts):

  rule       a new lesson                          undo: retired, and the topic is never learned again
  sharpen    a lesson's rule rewritten              undo: the old wording comes back
  mechanism  reflex -> always/guard, or back        undo: the old mechanism comes back
  retire     a lesson that never fired, or one too  undo: active again
             many
  fact       knowledge written into a note          undo: the added text leaves the note again

`brain review` lists the last two weeks, `brain undo <n>` takes one back. You have a veto, not a duty.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import Vault
from .util import append_jsonl, digest, host, now_iso, read_jsonl, short, today


def path(v: Vault) -> Path:
    return v.path("journal", f"{host()}.jsonl")


def log(v: Vault, kind: str, **data) -> dict:
    e = {"id": "j-" + digest(kind, now_iso(), str(sorted(data.items())))[:8], "t": now_iso(), "kind": kind, **data}
    append_jsonl(path(v), e)
    e["status"] = "done"
    return e


def entries(v: Vault) -> list[dict]:
    """Every change from every machine, oldest first, with status done or undone."""
    rows, undone = [], {}
    d = v.path("journal")
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        for e in read_jsonl(f):
            if e.get("kind") == "undo":
                undone[e.get("target")] = e.get("t")
            else:
                rows.append(e)
    for e in rows:
        e["status"] = "undone" if e.get("id") in undone else "done"
        if e["status"] == "undone":
            e["undone"] = undone[e["id"]]
    return sorted(rows, key=lambda e: str(e.get("t", "")))


def recent(v: Vault, days: int = 14, undone: bool = False) -> list[dict]:
    """Changes from the last `days` days, newest first: the ones still in force, and with `undone` the ones you took
    back too. That full list is what `brain undo <n>` counts, so the numbers do not shift after an undo."""
    from .util import days_since
    out = [e for e in entries(v) if (undone or e.get("status") == "done")
           and (days_since(str(e.get("t", ""))[:10]) or 0) <= days]
    return out[::-1]  # entries() keeps the written order within one second, so the last change is number 1


def count(v: Vault, lesson_id: str, kinds: tuple[str, ...]) -> int:
    return sum(1 for e in entries(v) if e.get("lesson") == lesson_id and e.get("kind") in kinds and e.get("status") == "done")


def since(v: Vault, first_day: str) -> list[dict]:
    return [e for e in entries(v) if e.get("status") == "done" and str(e.get("t", ""))[:10] >= first_day]


def describe(e: dict) -> tuple[str, str, str]:
    """(what, text, why) for one journal entry."""
    k = e.get("kind")
    if k == "rule":
        return "new rule", e.get("rule", ""), e.get("why", "")
    if k == "sharpen":
        return "sharper", e.get("after", ""), f"was: {short(e.get('before', ''), 90)}"
    if k == "mechanism":
        return f"{e.get('before')} → {e.get('after')}", e.get("rule", ""), e.get("why", "")
    if k == "retire":
        return "retired", e.get("rule", ""), e.get("why", "")
    if k == "fact":
        verb = {"new": "new note", "update": "updated", "append": "fact"}.get(e.get("action"), "fact")
        return verb, f"{e.get('note', '')} › {e.get('claim', '')}", e.get("why", "")
    return k or "?", "", ""


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def find(v: Vault, ref: str) -> dict | None:
    """An entry by id (j-…) or by its number in `brain review`."""
    ref = ref.strip()
    if ref.isdigit():  # newest first, so a longer window only adds rows at the end: the numbers stay the same
        rows = recent(v, days=366, undone=True)
        return rows[int(ref) - 1] if 0 < int(ref) <= len(rows) else None
    return next((e for e in entries(v) if e.get("id") == ref), None)


def undo(v: Vault, ref: str) -> str:
    """Take one change back. Returns what happened, raises ValueError when it cannot be done safely."""
    from . import learn
    from .notes import Library, atomic_write
    from .util import terms
    e = find(v, ref)
    if e is None:
        raise ValueError(f"nothing to undo for {ref!r} — `brain review` lists what can be undone")
    if e.get("status") != "done":
        what, text, _ = describe(e)
        raise ValueError(f"{what} “{short(text, 60)}” was already undone")
    kind, lid = e.get("kind"), e.get("lesson", "")
    if kind == "rule":
        lesson = next((l for l in Library(v.root).lessons(retired=True) if l.id == lid), None)
        if lesson is not None and not lesson.retired:
            learn.set_lesson_field(v, lid, "retired", f"{today()} · undone by you", by="undo")
        words = (lesson.words if lesson and len(lesson.words) >= 2 else terms(e.get("rule", ""))[:4])
        learn.block(v, words, f"undone: {short(e.get('rule', ''), 120)}")
        msg = f"rule {lid} retired; this topic is not learned again"
    elif kind == "sharpen":
        learn.set_lesson_field(v, lid, "rule", e["before"], by="undo")
        msg = f"{lid} has its old wording again"
    elif kind == "mechanism":
        learn.set_lesson_field(v, lid, "mechanism", e["before"], by="undo")
        msg = f"{lid} is {e['before']} again"
    elif kind == "retire":
        learn.set_lesson_field(v, lid, "retired", "", by="undo")
        msg = f"{lid} is active again"
    elif kind == "fact":
        p = v.root / e["path"]
        cur = p.read_text(encoding="utf-8") if p.exists() else None
        if e.get("action") == "new":
            if cur is None:
                msg = f"{e['path']} is already gone"
            elif sha(cur) != e.get("after"):
                raise ValueError(f"{e['path']} changed since the brain wrote it — edit it by hand")
            else:
                p.unlink()
                msg = f"removed {e['path']}"
            hub = e.get("hub")
            if hub and (v.root / hub).exists():
                h = (v.root / hub).read_text(encoding="utf-8")
                if e.get("hub_line") and e["hub_line"] in h:
                    atomic_write(v.root / hub, _drop_block(h, e["hub_line"]))
        else:
            added, old = e.get("text", ""), e.get("old")
            if cur is None or not added or cur.count(added) != 1:
                raise ValueError(f"{e['path']} changed since the brain wrote it — edit it by hand")
            if e.get("action") == "update" and old is not None:
                atomic_write(p, cur.replace(added, old, 1))
            else:
                atomic_write(p, _drop_block(cur, added))
            msg = f"{e['path']}: the brain's change is out again"
    else:
        raise ValueError(f"{e['id']} ({kind}) cannot be undone")
    append_jsonl(path(v), {"id": "j-" + digest("undo", e["id"], now_iso())[:8], "t": now_iso(), "kind": "undo",
                           "target": e["id"]})
    return msg


def _drop_block(text: str, block: str) -> str:
    """Remove an appended block and the blank line the append put before it. Only while it still stands as whole
    lines: a sentence you added to its line would otherwise end up glued to the line before."""
    i = text.find(block)
    end = i + len(block)
    if i < 0 or (i and text[i - 1] != "\n") or (end < len(text) and text[end] != "\n"):
        raise ValueError("the note changed around what the brain added — edit it by hand")
    for cand in ("\n\n" + block, "\n" + block, block):
        if cand in text:
            return text.replace(cand, "", 1)
    return text
