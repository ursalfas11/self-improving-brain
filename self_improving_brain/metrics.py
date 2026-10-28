"""Measure whether the brain actually gets smarter — and tune itself from the numbers.

Key numbers:
  recall precision   share of injected notes the agent then opened
  corrections/100    user corrections per 100 user messages, per ISO week (should fall)
  relapse            a correction on a lesson's topic *after* the lesson existed
                       ignored = lesson was shown in that session and still broken  -> sharpen rule
                       missed  = lesson was not shown                                -> widen triggers
  dormant            lesson never fired in 30+ days                                  -> drop or retrigger
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from . import events
from .config import Vault
from .notes import Library
from .util import days_since, save_json, terms


def _within(t0: str, t1: str, seconds: int) -> bool:
    """A read only teaches recall if it follows the prompt closely (same question)."""
    try:
        return 0 <= (dt.datetime.fromisoformat(t1) - dt.datetime.fromisoformat(t0)).total_seconds() <= seconds
    except ValueError:
        return False


def build_learned(v: Vault) -> dict:
    model: dict[str, dict] = defaultdict(lambda: {"shown": 0, "hit": 0, "terms": Counter()})
    last_terms: dict[str, list[str]] = {}
    recalled: dict[str, set] = defaultdict(set)
    hit_once: set = set()
    for e in events.all_events(v):
        s = e.get("session", "")
        if s == "doctor":  # `brain doctor` probes are not real questions
            continue
        k = e.get("k")
        if k == "recall":
            last_terms[s] = (str(e.get("t", "")), e.get("terms", [])[:8])
            for n in e.get("notes", []):
                model[n]["shown"] += 1
                recalled[s].add(n)
        elif k == "read":
            n = e.get("note")
            if not n:
                continue
            if n in recalled[s]:
                if (s, n) not in hit_once:
                    model[n]["hit"] += 1
                    hit_once.add((s, n))
            elif s in last_terms and _within(last_terms[s][0], str(e.get("t", "")), 900):
                for t in last_terms[s][1][:6]:
                    model[n]["terms"][t] += 1
    return {k: {"shown": m["shown"], "hit": m["hit"], "terms": dict(m["terms"].most_common(40))}
            for k, m in model.items()}


def recall_numbers(v: Vault, days: int = 30) -> dict:
    ev = [e for e in events.all_events(v)
          if (days_since(e.get("t", "")) or 0) <= days and e.get("session") != "doctor"]
    prompts = [e for e in ev if e.get("k") == "recall" and e.get("via") != "search"]
    shown = [(e.get("session"), n) for e in prompts for n in e.get("notes", [])]
    shown_set = set(shown)
    reads = {(e.get("session"), e.get("note")) for e in ev if e.get("k") == "read"}
    hits = len(shown_set & reads)
    finds = len({r for r in reads if r not in shown_set})
    return {"prompts": len(prompts), "injections": len(shown_set), "hits": hits, "finds": finds,
            "precision": round(hits / len(shown_set), 2) if shown_set else None,
            "fire_rate": round(sum(1 for e in prompts if e.get("notes")) / len(prompts), 2) if prompts else None}


def weekly_curve(v: Vault, weeks: int = 8) -> list[dict]:
    last: dict[str, dict] = {}
    for e in events.all_events(v, "turns"):
        last[e.get("session", "")] = e
    per = defaultdict(lambda: [0, 0])
    for e in last.values():
        w = e.get("week", "?")
        per[w][0] += e.get("user", 0)
        per[w][1] += e.get("corrections", 0)
    rows = [{"week": w, "messages": m, "corrections": c, "per100": round(100 * c / m, 1) if m else 0.0}
            for w, (m, c) in sorted(per.items())]
    return rows[-weeks:]


def lesson_health(v: Vault, lib: Library | None = None) -> list[dict]:
    lib = lib or Library(v.root)
    shown = Counter()
    last_shown: dict[str, str] = {}
    for e in events.all_events(v, "lesson"):
        shown[e.get("id")] += 1
        last_shown[e.get("id")] = e.get("t", "")
    corrections = events.all_events(v, "correction")
    out = []
    for l in lib.lessons():
        topic = set(terms(l.rule + " " + " ".join(l.words)))
        ignored = missed = 0
        for c in corrections:
            when = (c.get("ts") or c.get("t") or "")[:10]
            if l.since and when <= l.since:
                continue  # the mistake that created the lesson is not a relapse
            overlap = len(topic & set(terms(c.get("text", ""))))
            if overlap < 2:
                continue
            if l.id in c.get("lessons_shown", []):
                ignored += 1
            else:
                missed += 1
        age = days_since(l.since) if l.since else None
        verdict = "ok"
        if ignored >= 2:
            verdict = "sharpen rule or use guard"
        elif missed >= 2:
            verdict = "widen triggers"
        elif l.mechanism != "always" and shown[l.id] == 0 and age is not None and age >= 30:
            verdict = "dormant: retrigger or drop"
        out.append({"id": l.id, "note": l.note, "mechanism": l.mechanism, "shown": shown[l.id],
                    "last": last_shown.get(l.id, "")[:10], "ignored": ignored, "missed": missed,
                    "verdict": verdict})
    return out


def autotune(v: Vault) -> str | None:
    """Raise the recall threshold when injections are mostly ignored, lower it when they land."""
    r = recall_numbers(v)
    if r["injections"] < 30 or r["precision"] is None:
        return None
    th = float(v.get("recall_threshold"))
    new = th
    if r["precision"] < 0.35:
        new = th * 1.15
    elif r["precision"] > 0.7 and (r["fire_rate"] or 0) < 0.3:
        new = th * 0.9
    new = round(min(20.0, max(2.0, new)), 2)
    if new == th:
        return None
    v.set("recall_threshold", new)
    return f"recall threshold {th} -> {new} (precision {r['precision']:.0%} over {r['injections']} injections)"


def refresh_cache(v: Vault) -> None:
    save_json(v.path("state", "learned.json"), build_learned(v))


def summary_line(v: Vault) -> str:
    r = recall_numbers(v)
    curve = weekly_curve(v, 2)
    parts = []
    if r["precision"] is not None:
        parts.append(f"recall precision {r['precision']:.0%}")
    if curve:
        parts.append(f"corrections/100 msgs {curve[-1]['per100']} ({curve[-1]['week']})")
    return " · ".join(parts)
