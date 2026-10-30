"""Append-only event log, one file per machine (`.brain/events/<host>.jsonl`).

Every recall, read, lesson injection and correction lands here. This is what the brain learns
from, and because each machine writes its own file, syncing via git never conflicts.

Event kinds:
  recall   {session, notes, terms, scores}       notes injected for a prompt
  read     {session, note, via}                  agent opened a note (hit if recalled before)
  lesson   {session, id, trigger}                lesson injected or guard raised
  turns    {session, user, corrections, source}  per transcript: message and correction counts
  correction {session, text, week, draft, lessons_shown} a user correction found by `brain learn`
  violation {session, id, tool, sample}          a lesson's `check:` matched what the agent did
  tool     {cmd}                                 a CLI command was used

Counters that would flood the log (every hook call) are tallied per ISO week instead: `.brain/tally/<host>.json`.
"""
from __future__ import annotations

import time
from pathlib import Path

from .config import Vault
from .util import append_jsonl, host, load_json, now_iso, read_jsonl, save_json


def log(v: Vault, kind: str, **data) -> None:
    rec = {"t": now_iso(), "k": kind, "h": host()}
    rec.update(data)
    try:
        append_jsonl(v.path("events", f"{host()}.jsonl"), rec)
    except OSError:
        pass  # learning must never break the agent


def all_events(v: Vault, kind: str | None = None) -> list[dict]:
    out = []
    d = v.path("events")
    if d.exists():
        for f in sorted(d.glob("*.jsonl")):
            out.extend(read_jsonl(f))
    if kind:
        out = [e for e in out if e.get("k") == kind]
    out.sort(key=lambda e: str(e.get("t", "")))
    return out


def tally(v: Vault, **inc: int) -> None:
    """Add to this week's counters: inject_chars/inject_calls (what hooks put into context), checked/violations."""
    from .util import iso_week, today
    p = v.path("tally", f"{host()}.json")  # versioned like events: one file per machine
    data = load_json(p, {})
    if not isinstance(data, dict):
        data = {}
    w = data.setdefault(iso_week(today()), {})
    for k, n in inc.items():
        w[k] = int(w.get(k, 0)) + int(n)
    try:
        save_json(p, data)
    except OSError:
        pass


def tallies(v: Vault) -> dict:
    """{week: {counter: n}} summed over machines."""
    out: dict = {}
    d = v.path("tally")
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        data = load_json(f, {})
        for w, c in (data.items() if isinstance(data, dict) else []):
            o = out.setdefault(w, {})
            for k, n in (c.items() if isinstance(c, dict) else []):
                o[k] = o.get(k, 0) + int(n or 0)
    return dict(sorted(out.items()))


def count_check(v: Vault, violations: int) -> None:
    """Actions checked against self-checking rules: the denominator of the violation rate."""
    tally(v, checked=1, violations=violations)


def check_counts(v: Vault) -> dict:
    """{week: {checked, violations}} summed over machines."""
    return {w: {"checked": c.get("checked", 0), "violations": c.get("violations", 0)}
            for w, c in tallies(v).items() if c.get("checked")}


# --- per-session memory: "show each thing once per session" -------------------------------

def _session_file(v: Vault, session: str) -> Path:
    safe = "".join(c for c in (session or "nosession") if c.isalnum() or c in "-_")[:80]
    return v.path("state", "sessions", f"{safe}.json")


def session_state(v: Vault, session: str) -> dict:
    st = load_json(_session_file(v, session), {})
    if not isinstance(st, dict):
        st = {}
    for k in ("shown", "lessons"):
        if not isinstance(st.get(k), list):
            st[k] = []
    return st


def save_session_state(v: Vault, session: str, state: dict) -> None:
    save_json(_session_file(v, session), state)


def prune_sessions(v: Vault, max_age_days: int = 3) -> None:
    d = v.path("state", "sessions")
    if not d.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for f in d.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
