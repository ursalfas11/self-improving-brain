"""The answer check: is every answer true to your `always` rules?

`always` rules sit in every session from its first line, so they have no trigger to measure them by, and a rule
that is broken again and again only showed up when you complained. Now:

  check      after each answer (the Stop hook, detached, it never slows the agent) the LLM reads the last answer
             against the `always` rules. A breach counts only with a verbatim quote from the answer or from the
             tool calls of that turn; an invented one is dropped. Logged in state/answers.jsonl.
  feedback   a breach is shown once, in your next prompt of the same session, with the quote: concrete, where the
             rule itself sits far up at the session start.
  stats      per rule: answers checked, broken, share (`brain answers`): the comparison that was missing.
  sharpen    at night, a rule broken in at least a fifth of 20+ answers that does not get better is rewritten more
             concretely, once (same demand, quotes as evidence). Journaled; `brain undo` takes it back.

Off with `brain config answer_check false`. Uses llm_cmd and at most llm.SHARE["answers"] of the day's budget.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import events, journal, llm, trust
from .config import Vault
from .notes import Library, Lesson
from .transcripts import parse
from .util import append_jsonl, load_json, read_jsonl, save_json, short

MIN_WORDS = 25        # a shorter answer is not worth a call
ANSWER_MAX = 7000     # characters of the answer in the prompt (start and end)
FEEDBACK_H = 6        # a breach waits this long for the next prompt of its session
INEFFECTIVE_N = 20    # at least this many checks of a rule in INEFFECTIVE_DAYS ...
INEFFECTIVE_SHARE = 0.2  # ... at least this share broken ...
INEFFECTIVE_DAYS = 14    # ... and not clearly better than the INEFFECTIVE_DAYS before
SHARPEN_MAX = 2       # rules rewritten per night

PROMPT = llm.MARKER + """
You check the last answer a coding agent gave its user against the user's standing rules.

- checked: the ids of the rules that apply to this turn at all (a rule about waiting for CI applies only if
  something was waited for; a rule about how to answer applies to every answer).
- breaches: only clear breaches of a checked rule, each with a VERBATIM quote from the answer or the tool calls
  (at most 200 characters) and one sentence on what to do differently next time. When in doubt: no breach.
  What the user asked for explicitly (say, a long explanation) is no breach.

Return JSON: {"checked": ["<id>"], "breaches": [{"rule": "<id>", "quote": "<verbatim>", "better": "<one sentence>"}]}

## Rules
@RULES@

## The user's message
@USER@

## Tool calls in this turn
@CALLS@

## The agent's answer
@ANSWER@
"""

SHARPEN = llm.MARKER + """
The rule below is shown to a coding agent in every session, and the agent still breaks it often (examples below).
Rewrite it as ONE imperative sentence of at most 200 characters that is more concrete and harder to misread.
Same demand, nothing new, keep every name, command and number.
Return JSON: {"rule": "<the sharper rule>"}

Rule: @RULE@

Breaches:
@QUOTES@
"""


def _log(v: Vault) -> Path:
    return v.path("state", "answers.jsonl")


def _pending(v: Vault) -> Path:
    return v.path("state", "answers_pending.json")


def rules(v: Vault) -> list[Lesson]:
    """The rules every answer is checked against: the `always` rules that reach the agent."""
    return [l for l in trust.effective(v, Library(v.root).lessons(), record_seen=False) if l.mechanism == "always"]


def last_turn(path: Path) -> tuple[str, str, list[str], str]:
    """Transcript -> (user message, answer, tool calls, time) of the last turn: from the last message you typed."""
    tr = parse(path)
    if tr is None:
        return "", "", [], ""
    turns = tr.turns
    start = next((i for i in range(len(turns) - 1, -1, -1) if turns[i].role == "user" and turns[i].text.strip()), None)
    if start is None:
        return "", "", [], ""
    answer, calls = [], []
    for t in turns[start + 1:]:
        if t.role == "assistant" and t.text.strip():
            answer.append(t.text.strip())
        elif t.role == "tool_call":
            calls.append(f"[{t.tool}] {short(t.text, 200)}")
    return turns[start].text.strip(), "\n\n".join(answer), calls, turns[start].ts


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`_#>|]", "", t or "")).strip().lower()


def _cut(t: str, n: int) -> str:
    return t if len(t) <= n else t[: n // 2] + "\n[…]\n" + t[-n // 2:]


def check(v: Vault, path: Path, session: str = "") -> dict:
    """Check the last turn of one transcript. -> {"checked": [...], "breaches": [...]} or {"skipped": why}"""
    if not v.get("answer_check"):
        return {"skipped": "off"}
    if not llm.available(v):
        return {"skipped": "no llm_cmd"}
    user, answer, calls, ts = last_turn(Path(path))
    if len(answer.split()) < MIN_WORDS:
        return {"skipped": "short"}
    rs = rules(v)
    if not rs:
        return {"skipped": "no always rule"}
    sid = (session or Path(path).stem)[:8]
    key = f"{sid}|{ts}|{len(answer)}"
    last = load_json(v.path("state", "answers_last.json"), {})
    last = last if isinstance(last, dict) else {}
    if last.get(sid) == key:
        return {"skipped": "checked already"}
    prompt = (PROMPT.replace("@RULES@", "\n".join(f"[{l.id}] {l.rule}" for l in rs))
              .replace("@USER@", _cut(user, 2500)).replace("@CALLS@", "\n".join(calls[-40:]) or "(none)")
              .replace("@ANSWER@", _cut(answer, ANSWER_MAX)))
    try:
        with llm.purpose("answers"):
            res = llm.ask_json(v, prompt)
    except (llm.NoLLM, ValueError, OSError) as e:
        return {"skipped": short(str(e), 120)}
    if not isinstance(res, dict):
        return {"skipped": "no verdict"}
    ids = {l.id for l in rs}
    checked = sorted({i for i in res.get("checked") or [] if i in ids})
    seen_text = _norm(answer + "\n" + "\n".join(calls))
    breaches = []
    for b in res.get("breaches") or []:
        if not isinstance(b, dict) or b.get("rule") not in ids:
            continue
        quote = re.sub(r"\s+", " ", str(b.get("quote") or "")).strip()[:200]
        # only with a real quote: evidence the LLM made up does not count
        if len(quote) < 8 or _norm(quote.strip("“”\"'…. "))[:60] not in seen_text:
            continue
        breaches.append({"rule": b["rule"], "quote": quote, "better": short(str(b.get("better") or ""), 240)})
        if b["rule"] not in checked:
            checked.append(b["rule"])
    rec = {"t": int(time.time()), "s": sid, "checked": sorted(checked),
           "broken": sorted({b["rule"] for b in breaches}), "quotes": {b["rule"]: b["quote"] for b in breaches}}
    append_jsonl(_log(v), rec)
    last[sid] = key
    save_json(v.path("state", "answers_last.json"), dict(list(last.items())[-200:]))
    if breaches and sid:
        pend = load_json(_pending(v), {})
        pend = {k: x for k, x in (pend if isinstance(pend, dict) else {}).items()
                if time.time() - x.get("t", 0) < FEEDBACK_H * 3600}
        pend[sid] = {"t": int(time.time()), "breaches": breaches}
        save_json(_pending(v), pend)
    events.log(v, "answer_check", session=session, checked=len(checked), broken=len(breaches))
    return {"checked": rec["checked"], "breaches": breaches}


def feedback(v: Vault, session: str) -> str:
    """For the prompt hook: this session's open breaches as one line (shown once, then gone), else ''."""
    sid = (session or "")[:8]
    if not sid or not _pending(v).exists():
        return ""
    pend = load_json(_pending(v), {})
    if not isinstance(pend, dict) or sid not in pend:
        return ""
    r = pend.pop(sid)
    save_json(_pending(v), pend)
    if time.time() - r.get("t", 0) > FEEDBACK_H * 3600:
        return ""
    parts = [f"{b['rule']} — “{b['quote']}” → {str(b.get('better') or '').rstrip('. ')}" for b in r.get("breaches") or []]
    if not parts:
        return ""
    return ("BRAIN-ANSWER-CHECK (your last answer, checked against the always rules): " + " · ".join(parts)
            + ". Get it right this time; do not mention this note.")


def _records(v: Vault, since: float) -> list[dict]:
    return [r for r in read_jsonl(_log(v)) if r.get("t", 0) >= since]


def stats(v: Vault, days: int = 14, until: float | None = None) -> dict:
    """{rule id: {"checked": n, "broken": n, "share": q}} for the window [until - days, until)."""
    until = time.time() if until is None else until
    out: dict[str, dict] = {}
    for r in _records(v, until - days * 86400):
        if r.get("t", 0) >= until:
            continue
        for i in r.get("checked") or []:
            out.setdefault(i, {"checked": 0, "broken": 0})["checked"] += 1
        for i in r.get("broken") or []:
            out.setdefault(i, {"checked": 0, "broken": 0})["broken"] += 1
    for x in out.values():
        x["share"] = round(x["broken"] / x["checked"], 3) if x["checked"] else None
    return out


def ineffective(v: Vault) -> list[dict]:
    """`always` rules that are broken often and do not get better."""
    now = time.time()
    a = stats(v, INEFFECTIVE_DAYS, now)
    b = stats(v, INEFFECTIVE_DAYS, now - INEFFECTIVE_DAYS * 86400)
    live = {l.id for l in rules(v)}
    out = []
    for i, x in a.items():
        if i not in live or x["checked"] < INEFFECTIVE_N or (x["share"] or 0) < INEFFECTIVE_SHARE:
            continue
        before = b.get(i) or {}
        if before.get("checked", 0) >= INEFFECTIVE_N and before.get("share") is not None \
                and x["share"] < before["share"] * 0.75:
            continue  # clearly getting better: give it time
        out.append({"id": i, "checked": x["checked"], "broken": x["broken"], "share": x["share"],
                    "before": before.get("share")})
    return sorted(out, key=lambda r: -r["broken"])


def quotes(v: Vault, n: int = 3) -> dict[str, list[str]]:
    """The last breaches per rule, as evidence for a rewrite."""
    out: dict[str, list[str]] = {}
    for r in _records(v, time.time() - 30 * 86400):
        for i, q in (r.get("quotes") or {}).items():
            out.setdefault(i, []).append(q)
    return {i: q[-n:] for i, q in out.items()}


def sharpen(v: Vault) -> dict:
    """At night: rewrite each ineffective `always` rule more concretely, once per rule (also after an undo)."""
    from .learn import _suspicious, set_lesson_field
    out: dict = {"sharpened": [], "ineffective": 0}
    todo = ineffective(v)
    out["ineffective"] = len(todo)
    if not todo:
        return out
    done = {e.get("lesson") for e in journal.entries(v) if e.get("kind") == "sharpen" and e.get("source") == "answers"}
    by_id = {l.id: l for l in rules(v)}
    q = quotes(v)
    for r in todo:
        l = by_id.get(r["id"])
        if l is None or l.id in done or len(out["sharpened"]) >= SHARPEN_MAX:
            continue
        try:  # its own purpose: the day's checks must not leave the night without a call for this
            with llm.purpose("sharpen"):
                res = llm.ask_json(v, SHARPEN.replace("@RULE@", l.rule)
                                   .replace("@QUOTES@", "\n".join(f"- “{x}”" for x in q.get(l.id, [])) or "(none)"))
        except llm.NoLLM:
            break
        except (ValueError, OSError):
            continue
        new = short(str((res or {}).get("rule") or "").strip(), 240) if isinstance(res, dict) else ""
        if not new or new == l.rule or _suspicious(new):
            continue
        set_lesson_field(v, l.id, "rule", new, by="sharpened")
        journal.log(v, "sharpen", lesson=l.id, note=l.note, before=l.rule, after=new, source="answers",
                    why=f"broken in {r['broken']} of {r['checked']} checked answers although it is in every session")
        out["sharpened"].append(l.id)
    return out
