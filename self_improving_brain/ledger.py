"""Records more than one machine changes, kept as one file per machine (`.brain/<name>/<host>.jsonl`).

A draft or an inbox item changes over days (open -> held -> approved). In one shared file, two laptops that
sync the vault would rewrite the same lines on the same night, and git would stop the sync. So a machine
writes only its own file. When it changes a record another machine wrote, the changed copy goes into its
own file. Reading merges every machine's file: per id, the record that got furthest wins.
"""
from __future__ import annotations

from pathlib import Path

from .config import Vault
from .util import host, read_jsonl, write_jsonl


def own(v: Vault, name: str) -> Path:
    """This machine's file: the only one it writes."""
    old = v.path(name + ".jsonl")
    if old.exists():  # until 0.2 all machines shared one file: it becomes this machine's
        new = v.path(name, host() + ".jsonl")
        recs = read_jsonl(new)
        ids = {r.get("id") for r in recs}
        write_jsonl(new, recs + [r for r in read_jsonl(old) if r.get("id") not in ids])
        old.unlink()
    return v.path(name, host() + ".jsonl")


def files(v: Vault, name: str) -> list[Path]:
    """Every machine's file, this machine's first."""
    mine = own(v, name)
    others = sorted(p for p in mine.parent.glob("*.jsonl") if p != mine) if mine.parent.exists() else []
    return ([mine] if mine.exists() else []) + others


def _merge(paths: list[Path], rank: dict) -> dict:
    best: dict = {}
    for f in paths:
        for r in read_jsonl(f):
            k = r.get("id") or id(r)
            cur = best.get(k)
            if cur is None or rank.get(r.get("status"), 0) > rank.get(cur.get("status"), 0):
                best[k] = r
    return best


def load(v: Vault, name: str, rank: dict) -> list[dict]:
    """All machines' records: per id the one whose status got furthest; on a tie, this machine's."""
    return list(_merge(files(v, name), rank).values())


def save(v: Vault, name: str, records: list[dict], rank: dict) -> None:
    """Write this machine's file: what it already held, and every record of another machine that changed here."""
    mine = own(v, name)
    kept = {r.get("id") for r in read_jsonl(mine)}
    theirs = _merge([f for f in files(v, name) if f != mine], rank)
    write_jsonl(mine, [r for r in records if r.get("id") in kept or theirs.get(r.get("id") or id(r)) != r])
