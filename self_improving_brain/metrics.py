"""Measure whether the brain actually gets smarter — and tune itself from the numbers.

Key numbers:
  corrections/100    your corrections per 100 messages, per ISO week (should fall). Once the nightly judge has
                     looked at a moment and found it was no correction, it no longer counts.
  brain share        what the hooks put into your agent's context, as a share of the tokens your sessions used
  recall precision   share of injected notes the agent then opened
  relapse            a correction on a lesson's topic *after* the lesson existed
                       ignored = lesson was shown in that session and still broken  -> sharpen rule
                       missed  = lesson was not shown                                -> widen triggers
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from . import events
from .config import Vault
from .notes import Library
from .util import days_since, iso_week, read_jsonl, save_json, terms


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


def _sessions(v: Vault) -> dict[str, dict]:
    """The newest `turns` event per session (a session is re-read while it grows)."""
    last: dict[str, dict] = {}
    for e in events.all_events(v, "turns"):
        last[e.get("session", "")] = e
    return last


def weekly_curve(v: Vault, weeks: int = 8) -> list[dict]:
    noise = {d.get("id") for d in read_jsonl(v.path("drafts.jsonl")) if d.get("status") == "noise"}
    false_alarms = Counter(e.get("session", "") for e in events.all_events(v, "correction") if e.get("draft") in noise)
    per = defaultdict(lambda: [0, 0])
    for s, e in _sessions(v).items():
        w = e.get("week", "?")
        per[w][0] += e.get("user", 0)
        per[w][1] += max(0, e.get("corrections", 0) - false_alarms[s])
    rows = [{"week": w, "messages": m, "corrections": c, "per100": round(100 * c / m, 1) if m else 0.0}
            for w, (m, c) in sorted(per.items())]
    return rows[-weeks:]


def efficiency(v: Vault, weeks: int = 4) -> dict:
    """What the brain costs your agent: injected tokens against the tokens your sessions used (last `weeks`)."""
    tal = events.tallies(v)
    used = defaultdict(int)
    for e in _sessions(v).values():
        used[e.get("week", "?")] += int(e.get("tokens") or 0)
    keys = sorted(set(tal) | set(used))[-weeks:]
    injected = sum(int(tal.get(w, {}).get("inject_chars", 0)) for w in keys) // 4
    calls = sum(int(tal.get(w, {}).get("inject_calls", 0)) for w in keys)
    total = sum(used[w] for w in keys)
    return {"weeks": len(keys), "injected": injected, "calls": calls, "session_tokens": total,
            "share": round(injected / total, 5) if total else None}


def trend(curve: list[dict], min_messages: int = 30) -> float | None:
    """Change of the last full week against the four weeks before it, in percent (negative = fewer mistakes)."""
    rows = [r for r in curve if r["messages"] >= min_messages]
    this = iso_week(dt.date.today().isoformat())
    if rows and rows[-1]["week"] == this:
        rows = rows[:-1]  # the running week is not over yet
    if len(rows) < 2:
        return None
    last, before = rows[-1], rows[-5:-1]
    base = sum(r["corrections"] for r in before) / max(1, sum(r["messages"] for r in before)) * 100
    return round(100 * (last["per100"] - base) / base) if base else None


def week_digest(v: Vault) -> dict:
    """What the brain did in the last seven days, for the weekly line and `brain`."""
    from . import journal, llm
    first = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    js = journal.since(v, first)
    kinds = Counter(e.get("kind") for e in js)
    eff = efficiency(v)
    return {"rules": kinds["rule"], "sharpened": kinds["sharpen"] + kinds["mechanism"], "retired": kinds["retire"],
            "facts": kinds["fact"], "trend": trend(weekly_curve(v, 12)), "share": eff["share"],
            "cost": llm.spent_since(v, first)}


def since_start(curve: list[dict], min_messages: int = 30) -> dict | None:
    """The last full week against your first two weeks of data (the baseline from before the brain knew you)."""
    rows = [r for r in curve if r["messages"] >= min_messages]
    if rows and rows[-1]["week"] == iso_week(dt.date.today().isoformat()):
        rows = rows[:-1]
    if len(rows) < 3:
        return None
    base = 100 * sum(r["corrections"] for r in rows[:2]) / sum(r["messages"] for r in rows[:2])
    if not base:
        return None
    y, w = (int(x) for x in rows[0]["week"].split("-W"))
    return {"now": rows[-1]["per100"], "base": round(base, 1), "change": round(100 * (rows[-1]["per100"] - base) / base),
            "since": dt.date.fromisocalendar(y, w, 1).isoformat()}


def learned_text(d: dict) -> str:
    parts = [f"{d['rules']} new rule{'s' * (d['rules'] != 1)}" if d["rules"] else "",
             f"{d['sharpened']} sharpened" if d["sharpened"] else "",
             f"{d['facts']} fact{'s' * (d['facts'] != 1)} filed" if d["facts"] else "",
             f"{d['retired']} retired" if d["retired"] else ""]
    return " · ".join(x for x in parts if x) or "nothing new to learn"


def digest_line(d: dict) -> str:
    parts = [learned_text(d)]
    if d.get("trend") is not None:
        parts.append(f"mistakes {'−' if d['trend'] < 0 else '+'}{abs(d['trend'])} %")
    if d.get("share") is not None:
        parts.append(f"brain cost {100 * d['share']:.1f} % of your tokens")
    return "🧠 This week: " + " · ".join(parts) + " — `brain review` shows it, undo anything."


def lesson_health(v: Vault, lib: Library | None = None) -> list[dict]:
    lib = lib or Library(v.root)
    shown = Counter()
    last_shown: dict[str, str] = {}
    for e in events.all_events(v, "lesson"):
        shown[e.get("id")] += 1
        last_shown[e.get("id")] = e.get("t", "")
    corrections = events.all_events(v, "correction")
    violations = Counter(e.get("id") for e in events.all_events(v, "violation"))
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
        if violations[l.id] >= 2 and l.mechanism in ("reflex", "skill", "always"):
            verdict = f"broken {violations[l.id]}× despite the rule → brain promote {l.id} --to guard"
        elif ignored >= 2:
            verdict = "sharpen rule or use guard"
        elif missed >= 2:
            verdict = "widen triggers"
        elif l.mechanism != "always" and shown[l.id] == 0 and age is not None and age >= 30:
            verdict = "dormant: retrigger or drop"
        out.append({"id": l.id, "note": l.note, "mechanism": l.mechanism, "shown": shown[l.id],
                    "last": last_shown.get(l.id, "")[:10], "ignored": ignored, "missed": missed,
                    "violations": violations[l.id], "check": bool(l.check),
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
