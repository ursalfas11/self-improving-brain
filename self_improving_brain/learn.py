"""The learning loop: mistakes -> drafts -> (your OK) -> lessons -> injected at the right moment -> measured.

The model itself never learns. What gets better is *what it is told, when*. `brain learn`
reads your session transcripts, finds the moments you corrected the agent or rejected a tool
call, and turns them into lesson drafts. You (or your agent, with you watching) approve them;
an approved lesson becomes a `> [!lesson]` callout in the note where the topic already lives.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from . import events, llm
from .config import Vault
from .notes import Lesson, Library, atomic_write, dump_frontmatter, set_field
from .transcripts import parse
from .util import (digest, iso_week, load_json, now_iso, read_jsonl, save_json, short, slugify,
                   terms, today, write_jsonl, append_jsonl)

CORRECTION = re.compile(
    r"^\s*(no+|nope|wrong|stop|wait|hm+|huh|nein|ne+|nö|falsch|stopp?|halt|hä+|moment)\b[\s,.!?:]"
    r"|\b(that'?s not|that is not|not what i|i said|i told you|you didn'?t|you forgot|why did you|"
    r"you should have|that'?s wrong|incorrect|still (broken|failing|wrong)|doesn'?t work|where is|"
    r"don'?t do|did i ask|again\?|"
    r"das ist nicht|nicht das|hab ich doch|ich hatte (doch )?gesagt|ich sagte|warum hast du|wieso hast du|"
    r"schon wieder|geht nicht|funktioniert nicht|wo ist|ja aber|hast du (nicht|vergessen)|sollst nicht)\b",
    re.I)
REMEMBER = re.compile(
    r"\b(remember (that|this|to)|from now on|going forward|in (the )?future|always make sure|never again|"
    r"merk dir|merke dir|ab jetzt|ab sofort|in zukunft|künftig|kuenftig)\b", re.I)


def drafts_path(v: Vault) -> Path:
    return v.path("drafts.jsonl")


def drafts(v: Vault, status: str | None = "open") -> list[dict]:
    d = read_jsonl(drafts_path(v))
    return [x for x in d if status is None or x.get("status") == status]


def rejected_topics(v: Vault) -> list[dict]:
    return read_jsonl(v.path("rejected.jsonl"))


def _blocked(text: str, topics: list[dict]) -> bool:
    t = set(terms(text))
    for top in topics:
        words = [w.lower() for w in top.get("words", [])]
        if sum(1 for w in words if w in t) >= min(2, len(words) or 1):
            return True
    return False


def _context_before(turns, i: int) -> str:
    """The agent action the user reacted to: last assistant text or tool call before turn i."""
    for t in reversed(turns[:i]):
        if t.role == "tool_call":
            return f"[{t.tool}] {short(t.text, 220)}"
        if t.role == "assistant":
            return short(t.text, 260)
    return ""


def transcript_files(v: Vault) -> list[Path]:
    out = []
    for pattern in v.get("transcripts") or []:
        out.extend(Path(p) for p in glob.glob(os.path.expanduser(pattern), recursive=True))
    return sorted(set(out), key=lambda p: p.stat().st_mtime if p.exists() else 0)


def scan(v: Vault, files: list[Path] | None = None, max_age_days: int = 21) -> dict:
    seen = load_json(v.path("state", "learn_seen.json"), {"files": {}, "items": []})
    seen_items = set(seen.get("items", []))
    topics = rejected_topics(v)
    lesson_events = events.all_events(v, "lesson")
    shown_by_session: dict[str, set] = {}
    for e in lesson_events:
        shown_by_session.setdefault(e.get("session", ""), set()).add(e.get("id"))

    import time
    cutoff = time.time() - max_age_days * 86400
    stats = {"transcripts": 0, "user_messages": 0, "corrections": 0, "new_drafts": 0}
    new: list[dict] = []
    for f in files if files is not None else transcript_files(v):
        try:
            st = f.stat()
        except OSError:
            continue
        sig = f"{st.st_mtime:.0f}:{st.st_size}"
        if files is None and (st.st_mtime < cutoff or seen["files"].get(str(f)) == sig):
            continue
        tr = parse(f)
        seen["files"][str(f)] = sig
        if tr is None or not tr.turns:
            continue
        stats["transcripts"] += 1
        n_user = n_corr = 0
        for i, t in enumerate(tr.turns):
            kind = None
            if t.role == "user":
                n_user += 1
                head = t.text[:160]
                if REMEMBER.search(t.text[:400]):
                    kind = "preference"
                elif CORRECTION.search(head) and len(t.text) < 1500:
                    kind = "lesson"
                    n_corr += 1
            elif t.role == "tool_result" and t.rejected:
                kind = "lesson"
                n_corr += 1
            if not kind:
                continue
            quote = t.text if t.role == "user" else "(tool call rejected)"
            if t.role == "tool_result":
                nxt = next((x for x in tr.turns[i + 1:i + 3] if x.role == "user"), None)
                if nxt:
                    quote = nxt.text
            key = digest(str(tr.session), quote[:300], str(t.ts or ""))
            if key in seen_items:
                continue
            seen_items.add(key)
            week = iso_week(t.ts or today())
            if kind == "lesson":
                events.log(v, "correction", session=tr.session, week=week, ts=t.ts, text=short(quote, 300),
                           lessons_shown=sorted(shown_by_session.get(tr.session, set())))
            if _blocked(quote, topics):
                continue
            new.append({"id": "d-" + key[:8], "t": now_iso(), "kind": kind, "status": "open",
                        "quote": short(quote, 500), "context": _context_before(tr.turns, i),
                        "session": tr.session, "source": tr.source, "ts": t.ts, "rule": ""})
        stats["user_messages"] += n_user
        stats["corrections"] += n_corr
        events.log(v, "turns", session=tr.session, source=tr.source, user=n_user,
                   corrections=n_corr, week=iso_week((tr.turns[0].ts or today())))
    for d in new:
        append_jsonl(drafts_path(v), d)
    stats["new_drafts"] = len(new)
    seen["items"] = sorted(seen_items)[-20000:]
    save_json(v.path("state", "learn_seen.json"), seen)
    if new and llm.available(v):
        stats["refined"] = refine(v)
    return stats


REFINE_PROMPT = """You maintain the lesson memory of a coding/work agent. Below are moments from real
sessions where the user corrected the agent, rejected a tool call or asked it to remember something.
For EACH item decide:
- keep: true only if it reveals a reusable rule (a mistake likely to repeat, or a lasting preference).
  Plain follow-up questions, typos, one-off task details: keep false.
- rule: ONE imperative sentence, max 200 chars, that would have prevented it. Concrete, no fluff.
- mechanism: "reflex" (inject when triggers match; default), "always" (applies to every session),
  "guard" (ask the user before an irreversible tool action).
- words: 2-5 prompt keywords that signal the situation. tools: tool name suffixes, if any.
  commands: a Python regex on the shell command head, if relevant, else "".
- class: a short kebab-case mistake class, e.g. "unverified-claim", "scope-creep", "skipped-test".
Return a JSON list: [{"id": ..., "keep": bool, "rule": ..., "mechanism": ..., "words": [...],
"tools": [...], "commands": "", "class": ...}]

Items:
"""


def refine(v: Vault, limit: int = 25) -> int:
    all_d = read_jsonl(drafts_path(v))
    todo = [d for d in all_d if d.get("status") == "open" and not d.get("rule")][:limit]
    if not todo:
        return 0
    body = "\n".join(f'- id={d["id"]} kind={d["kind"]}\n  agent did: {d["context"]}\n  user said: {d["quote"]}'
                     for d in todo)
    try:
        res = llm.ask_json(v, REFINE_PROMPT + body)
    except (llm.NoLLM, ValueError, OSError) as e:
        events.log(v, "error", where="refine", msg=str(e)[:200])
        return 0
    by_id = {r.get("id"): r for r in res if isinstance(r, dict)}
    n = 0
    for d in all_d:
        r = by_id.get(d["id"])
        if not r:
            continue
        n += 1
        if not r.get("keep"):
            d["status"] = "noise"
            continue
        d["rule"] = short(str(r.get("rule", "")), 220)
        d["suggest"] = {k: r.get(k) for k in ("mechanism", "words", "tools", "commands", "class")}
    write_jsonl(drafts_path(v), all_d)
    return n


# --- approving: a draft becomes a lesson inside the note that owns the topic -----------------

def unique_lesson_id(lib: Library, rule: str, klass: str = "") -> str:
    base = "L-" + (slugify(klass) + "-" if klass else "") + slugify(" ".join(terms(rule)[:4]))
    existing = {l.id for l in lib.lessons()}
    lid, i = base, 2
    while lid in existing:
        lid, i = f"{base}-{i}", i + 1
    return lid


def default_note(v: Vault) -> Path:
    p = v.root / "20-areas" / "working-style.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump_frontmatter({"name": "working-style", "description":
                     "How I want my agents to work: lessons that apply across projects.",
                     "type": "preference", "updated": today()}) +
                     "\n# Working style\n\nRules learned from real sessions. Linked from [[hub-brain]].\n",
                     encoding="utf-8")
    return p


def write_lesson(v: Vault, note_path: Path, lesson: Lesson) -> None:
    text = set_field(note_path.read_text(encoding="utf-8"), "updated", today())
    atomic_write(note_path, text.rstrip() + "\n\n" + lesson.as_callout() + "\n")


def approve(v: Vault, draft_id: str, *, rule: str = "", mechanism: str = "", note: str = "",
            words: list[str] | None = None, tools: list[str] | None = None, commands: str = "",
            paths: str = "", klass: str = "") -> Lesson:
    all_d = read_jsonl(drafts_path(v))
    d = next((x for x in all_d if x["id"] == draft_id), None)
    if d is None:
        raise KeyError(f"no draft {draft_id}")
    sug = d.get("suggest") or {}
    rule = rule or d.get("rule") or ""
    if not rule:
        raise ValueError("this draft has no rule yet — pass --rule \"<one imperative sentence>\"")
    lib = Library(v.root)
    target = lib.find(note) if note else None
    if note and target is None:
        raise KeyError(f"note {note!r} not found")
    if target is None:
        from .search import Index
        hits = [h for h in Index(lib).score(rule + " " + d.get("quote", ""), include_hubs=False)
                if h.score >= 2 * float(v.get("recall_threshold"))]
        target = hits[0].note if hits else None
    note_path = target.path if target else default_note(v)
    lesson = Lesson(
        id=unique_lesson_id(lib, rule, klass or sug.get("class") or ""),
        note=target.name if target else "working-style",
        rule=rule, mechanism=mechanism or sug.get("mechanism") or "reflex",
        tools=tools if tools is not None else list(sug.get("tools") or []),
        commands=commands or sug.get("commands") or "", paths=paths,
        words=words if words is not None else list(sug.get("words") or terms(d.get("quote", ""))[:4]),
        since=today(), evidence=short(d.get("quote", ""), 140).replace("\n", " "),
        klass=klass or sug.get("class") or "")
    write_lesson(v, note_path, lesson)
    d["status"] = "approved"
    d["lesson"] = lesson.id
    write_jsonl(drafts_path(v), all_d)
    return lesson


def reject(v: Vault, draft_id: str, block_words: list[str] | None = None, reason: str = "") -> bool:
    all_d = read_jsonl(drafts_path(v))
    ok = False
    for d in all_d:
        if d["id"] == draft_id:
            d["status"] = "rejected"
            ok = True
    write_jsonl(drafts_path(v), all_d)
    if ok and block_words:
        append_jsonl(v.path("rejected.jsonl"), {"t": now_iso(), "words": block_words, "reason": reason})
    return ok
