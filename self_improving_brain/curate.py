"""Self-cleaning: the rule set stays small, current and sharp without anyone tending it.

Runs every night after learning. Every change goes into the journal and can be undone.

  quiet      a reflex rule that has not fired for `retire_after_days` (and is that old) retires: its words do
             not match how you work, so it only takes up room. Guards and always-rules never retire for
             being quiet: a guard waits for rare moments, an always-rule fires every session anyway.
  too many   above `always_max` always-rules, the least recent ones become reflexes again; above
             `lessons_max` active rules, the ones that fired least recently retire first.

Contradictions are settled while learning: a newer rule on the same topic replaces the wording of the older one.
"""
from __future__ import annotations

from . import events, journal, learn
from .config import Vault
from .notes import Library
from .util import days_since, today


def _last_fired(v: Vault) -> dict[str, str]:
    last: dict[str, str] = {}
    for e in events.all_events(v):
        if e.get("k") in ("lesson", "violation") and e.get("session") != "doctor" and e.get("id"):
            last[e["id"]] = str(e.get("t", ""))
    return last


def run(v: Vault) -> dict:
    lib = Library(v.root)
    active = lib.lessons()
    last = _last_fired(v)
    days = int(v.get("retire_after_days"))
    out = {"retired": [], "demoted": []}

    def retire(l, why: str) -> None:
        learn.set_lesson_field(v, l.id, "retired", f"{today()} · {why}", by="retired")
        journal.log(v, "retire", lesson=l.id, note=l.note, rule=l.rule, why=why)
        out["retired"].append(l.id)

    for l in active:
        if l.mechanism not in ("reflex", "skill"):
            continue
        age = days_since(l.since) if l.since else None
        quiet = days_since(last[l.id][:10]) if l.id in last else age
        if age is not None and age >= days and quiet is not None and quiet >= days:
            retire(l, f"not needed in {days} days")

    left = [l for l in active if l.id not in out["retired"]]
    always = sorted((l for l in left if l.mechanism == "always"), key=lambda l: last.get(l.id, l.since or ""))
    for l in always[: max(0, len(always) - int(v.get("always_max")))]:
        learn.set_lesson_field(v, l.id, "mechanism", "reflex", by="demoted")
        journal.log(v, "mechanism", lesson=l.id, note=l.note, rule=l.rule, before="always", after="reflex",
                    why=f"more than {v.get('always_max')} rules for every session; this one fired least recently")
        out["demoted"].append(l.id)

    over = len(left) - int(v.get("lessons_max"))
    if over > 0:
        spare = sorted((l for l in left if l.mechanism in ("reflex", "skill") and l.id not in out["demoted"]),
                       key=lambda l: last.get(l.id, l.since or ""))
        for l in spare[:over]:
            retire(l, f"more than {v.get('lessons_max')} active rules; this one fired least recently")
    return out
