"""The inbox: everything new becomes a single, self-contained claim first — never a note directly.

Item types: fact, decision, status, pitfall, preference, todo, lesson.
An item is distilled into the wiki later (by your agent or by the nightly run).
"""
from __future__ import annotations

from . import ledger
from .config import Vault
from .secrets import scan
from .util import append_jsonl, digest, now_iso, read_jsonl, terms

TYPES = ("fact", "decision", "status", "pitfall", "preference", "todo", "lesson")
RANK = {"open": 0, "done": 1, "skipped": 1, "dropped": 1}  # a closed item stays closed on every machine


class Rejected(ValueError):
    pass


def path(v: Vault):
    """This machine's inbox (one file per machine, see ledger.py)."""
    return ledger.own(v, "inbox")


def items(v: Vault, include_done: bool = False, mine: bool = False) -> list[dict]:
    """Every machine's items; `mine`: only those this machine filed (the nightly run files only its own)."""
    out = ledger.load(v, "inbox", RANK)
    if mine:
        ids = {i.get("id") for i in read_jsonl(path(v))}
        out = [i for i in out if i.get("id") in ids]
    return out if include_done else [i for i in out if i.get("status", "open") == "open"]


def _similar(a: str, b: str) -> float:
    ta, tb = set(terms(a)), set(terms(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def add(v: Vault, typ: str, claim: str, *, source: str = "cli", quote: str = "",
        note: str = "", session: str = "") -> dict:
    typ = typ.lower()
    if typ not in TYPES:
        raise Rejected(f"unknown type {typ!r}; use one of: {', '.join(TYPES)}")
    claim = " ".join(claim.split())
    if len(claim) < 8:
        raise Rejected("claim too short — write one sentence that makes sense on its own")
    hits = scan(claim + "\n" + quote)
    if hits:
        raise Rejected("refused: looks like a secret (" + ", ".join(k for k, _ in hits) + ")."
                       " Store the variable name and where the value lives, not the value.")
    for it in items(v):
        if _similar(it["claim"], claim) >= 0.8:
            raise Rejected(f"already in inbox as {it['id']}: {it['claim']}")
    rec = {"id": "i-" + digest(typ, claim), "t": now_iso(), "type": typ, "claim": claim,
           "quote": quote, "source": source, "note": note, "session": session, "status": "open"}
    append_jsonl(path(v), rec)
    return rec


def close(v: Vault, item_id: str, status: str = "done", result: str = "") -> bool:
    all_items = items(v, include_done=True)
    ok = False
    for it in all_items:
        if it["id"] == item_id and it.get("status", "open") == "open":
            it["status"] = status
            it["closed"] = now_iso()
            if result:
                it["result"] = result
            ok = True
    if ok:
        ledger.save(v, "inbox", all_items, RANK)
    return ok
