"""Vault hygiene. MUST findings block trust (secrets, dead links); SHOULD findings are upkeep.

"One truth" rule: every fact lives in exactly one note. The near-duplicate check is the
cheap guard against the same thing drifting apart in two places.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .config import Vault
from .notes import Library, Note
from .secrets import scan
from .util import days_since, terms


@dataclass
class Finding:
    level: str   # MUST | SHOULD
    note: str
    msg: str

    def __str__(self) -> str:
        return f"{self.level:6} {self.note}: {self.msg}"


def lint_note(n: Note, lib: Library, stale_days: int = 45) -> list[Finding]:
    out = []
    for kind, sample in scan(n.body + "\n" + n.description):
        out.append(Finding("MUST", n.rel, f"possible secret ({kind}: {sample})"))
    for link in n.links:
        if lib.resolve(link) is None:
            out.append(Finding("MUST", n.rel, f"dead link [[{link}]]"))
    if not n.description:
        out.append(Finding("SHOULD", n.rel, "no description in frontmatter (recall and index rely on it)"))
    if not n.is_hub and not n.links:
        out.append(Finding("SHOULD", n.rel, "orphan: links to nothing — add its hub, e.g. [[hub-…]]"))
    if n.type == "project" and n.updated and (days_since(n.updated) or 0) > stale_days:
        out.append(Finding("SHOULD", n.rel, f"stale: project not updated for {days_since(n.updated)} days"))
    for l in n.lessons:
        if len(l.rule) > 240:
            out.append(Finding("SHOULD", n.rel, f"{l.id}: rule longer than 240 chars"))
        if l.mechanism in ("reflex", "guard", "skill") and not (l.words or l.tools or l.commands or l.paths):
            out.append(Finding("SHOULD", n.rel, f"{l.id}: {l.mechanism} lesson without any trigger"))
    return out


def lint(v: Vault, lib: Library | None = None) -> list[Finding]:
    lib = lib or Library(v.root)
    out: list[Finding] = []
    stale = int(v.get("stale_days"))
    for n in lib.notes.values():
        out.extend(lint_note(n, lib, stale))
    ids = {}
    for l in lib.lessons():
        if l.id in ids:
            out.append(Finding("MUST", l.note, f"lesson id {l.id} also used in {ids[l.id]}"))
        ids[l.id] = l.note
    always = [l for l in lib.lessons() if l.mechanism == "always"]
    if len(always) > int(v.get("always_max")):
        out.append(Finding("SHOULD", "(lessons)", f"{len(always)} 'always' lessons (max {v.get('always_max')}) —"
                           " every one costs tokens in every session; turn some into reflexes"))
    out.extend(duplicates(lib))
    return sorted(out, key=lambda f: (f.level != "MUST", f.note))


def duplicates(lib: Library, threshold: float = 0.7) -> list[Finding]:
    sig = {k: set(terms(n.title + " " + n.description)) for k, n in lib.notes.items() if not n.is_hub}
    out = []
    for a, b in combinations(sorted(sig), 2):
        sa, sb = sig[a], sig[b]
        if len(sa) < 4 or len(sb) < 4:
            continue
        j = len(sa & sb) / len(sa | sb)
        if j >= threshold:
            out.append(Finding("SHOULD", lib.notes[a].rel, f"looks like a duplicate of [[{b}]] ({j:.0%} overlap) — merge into one truth"))
    return out
