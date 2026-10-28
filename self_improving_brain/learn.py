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

# Correction signals, strongest first. Measured on real sessions: a bare "no"/"ne" at the start
# is most often an *answer* to a question the agent asked, so it only counts when the agent did
# not just ask one. Strong phrases ("why did you", "I told you") count anywhere in the opening.
STRONG = re.compile(
    r"\b(why did you|why would you|i told you|i said|i already said|you should(n'?t)? have|you were supposed to|"
    r"not what i (asked|said|meant|wanted)|that'?s not what|did i ask|how many times|stop doing|don'?t ever|"
    r"never do that|you did it again|same mistake|again\?!|"
    r"warum hast du|wieso hast du|weshalb hast du|ich hatte (dir )?(doch )?gesagt|ich sagte doch|hab(e)? ich (dir )?doch gesagt|"
    r"hab(e)? ich nicht gesagt|hab(e)? ich nicht gefragt|nicht schon wieder|"
    r"das war falsch|das ist falsch|falsch verstanden|wie oft noch)(?!\w)", re.I)
MEDIUM = re.compile(
    r"\b(that'?s wrong|that is wrong|incorrect|doesn'?t work|does not work|still (broken|failing|wrong)|"
    r"you forgot|you didn'?t|you missed|you broke|no,? you|du sollst|du solltest|schon wieder|"
    r"hast du (vergessen|nicht)|geht (immer )?noch nicht|geht nicht|funktioniert (immer )?(noch )?nicht|klappt nicht|"
    r"nein,? du|ne,? du|stimmt nicht)(?!\w)", re.I)
# Openers that are as often an *answer* to the agent's question as a correction ("No, that's fine").
START = re.compile(
    r"^\s*(no+|nope|wrong|stop|wait|huh|nein|ne+|nö|falsch|stopp?|halt|hä+|moment)(?:[\s,.!?:]|$)|"
    r"\b(no,? that|nein,? das|ne,? das|yes,? but|ja,? aber|actually,)(?!\w)", re.I)
REMEMBER = re.compile(
    r"\b(remember (that|this|to)|keep in mind|from now on|going forward|in (the )?future|always make sure|never again|"
    r"merk dir|merke dir|denk dran|ab jetzt|ab sofort|in zukunft|künftig|kuenftig)\b", re.I)
LASTING = re.compile(
    r"\b(always|never|every time|each time|by default|from now on|going forward|in (the )?future|"
    r"immer|nie|niemals|jedes mal|grundsätzlich|generell|standardmäßig|ab jetzt|ab sofort|in zukunft|künftig)\b", re.I)
ACCIDENT = re.compile(r"^\s*(sorry|sry|sorri|oops|ups|weiter|continue|go on|mach weiter|ok(ay)?|passt|egal|"
                      r"aus versehen|versehentlich|my bad|mistake)\b", re.I)
DRAFT_MIN = 0.4


def correction_score(text: str, agent_asked: bool) -> float:
    head = text[:220]
    if STRONG.search(head):
        return 0.85
    if MEDIUM.search(head):
        return 0.55
    if START.search(head):
        if agent_asked or head.rstrip().endswith("?"):
            return 0.15  # an answer to the agent's question, not a correction
        return 0.45
    return 0.0


def _agent_asked(turns, i: int) -> bool:
    for t in reversed(turns[:i]):
        if t.role == "assistant":
            return "?" in t.text[-400:]
        if t.role == "user":
            return False
    return False


def classify(turns, i: int):
    """(kind, score, quote) for turn i, or None. kind: lesson | preference."""
    t = turns[i]
    if t.role == "user":
        if len(t.text) > 1500:
            return None
        if REMEMBER.search(t.text[:400]):
            return ("preference", 0.8 if LASTING.search(t.text[:400]) else 0.35, t.text)
        sc = correction_score(t.text, _agent_asked(turns, i))
        return ("lesson", sc, t.text) if sc else None
    if t.role == "tool_result" and t.rejected:
        nxt = next((x for x in turns[i + 1:i + 4] if x.role == "user"), None)
        if nxt is None:
            return ("lesson", 0.4, "(tool call rejected)")
        if ACCIDENT.search(nxt.text[:60]):
            return None
        sc = correction_score(nxt.text, False)
        if sc >= 0.45:
            return ("lesson", max(0.75, sc), nxt.text)
        return ("lesson", 0.42 if len(nxt.text.split()) >= 4 else 0.3, nxt.text)
    return None


def _score(d: dict) -> float:
    try:
        return float(d.get("score", 0.5))
    except (TypeError, ValueError):
        return 0.5


def _overlap(ta: set, tb: set) -> float:
    m = min(len(ta), len(tb))
    return len(ta & tb) / m if m >= 3 else 0.0


def similarity(a: str, b: str) -> float:
    return _overlap(set(terms(a)), set(terms(b)))


def groups(ds: list[dict], threshold: float = 0.5, limit: int = 500) -> list[list[dict]]:
    """Cluster similar drafts. A mistake you corrected four times beats one you corrected once.
    Only the `limit` strongest drafts are clustered, so a review backlog never slows a session start."""
    out: list[tuple[set, list[dict]]] = []
    for d in sorted(ds, key=lambda x: -_score(x))[:limit]:
        t = set(terms(d.get("rule") or d.get("quote", "")))
        for key, g in out:
            if _overlap(t, key) >= threshold:
                g.append(d)
                break
        else:
            out.append((t, [d]))
    gs = [g for _, g in out]
    gs.sort(key=lambda g: -(max(_score(x) for x in g) + 0.15 * (len(g) - 1)))
    return gs


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
    """Mine transcripts not seen before. Each file is read once (signature: mtime + size)."""
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
        if tr.internal:
            continue  # our own headless LLM calls must never be mined as "user corrections"
        stats["transcripts"] += 1
        n_user = n_corr = 0
        counted: set[int] = set()
        for i, t in enumerate(tr.turns):
            if t.role == "user":
                n_user += 1
            c = classify(tr.turns, i)
            if not c or c[1] < DRAFT_MIN:
                continue
            kind, score, quote = c
            if kind == "lesson":
                # a rejection and the user's explanation right after it are one correction
                j = next((k for k in range(i, min(i + 4, len(tr.turns))) if tr.turns[k].role == "user"), i)
                if j in counted or i in counted:
                    continue
                counted.update({i, j})
                n_corr += 1
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
            new.append({"id": "d-" + key[:8], "t": now_iso(), "kind": kind, "status": "open", "score": score,
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


REFINE_PROMPT = llm.MARKER + """
You maintain the lesson memory of a coding/work agent. Below are moments from real
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


def refine(v: Vault, limit: int = 25, batches: int = 4) -> int:
    """Let the configured LLM judge drafts in batches: keep real, reusable mistakes, write the rule."""
    total = 0
    for _ in range(batches):
        n = _refine_batch(v, limit)
        if not n:
            break
        total += n
    return total


def _refine_batch(v: Vault, limit: int) -> int:
    todo = sorted((d for d in read_jsonl(drafts_path(v))
                   if d.get("id") and d.get("status") == "open" and not d.get("rule") and not d.get("judged")),
                  key=lambda d: -_score(d))[:limit]
    if not todo:
        return 0
    body = "\n".join(f'- id={d["id"]} kind={d.get("kind", "lesson")}\n  agent did: {d.get("context", "")}\n'
                     f'  user said: {d.get("quote", "")}' for d in todo)
    try:
        res = llm.ask_json(v, REFINE_PROMPT + body)
    except (llm.NoLLM, ValueError, OSError) as e:
        events.log(v, "error", where="refine", msg=str(e)[:200])
        return 0
    if isinstance(res, dict):
        res = res.get("items") or [res]
    by_id = {r.get("id"): r for r in res if isinstance(r, dict)}
    todo_ids = {d["id"] for d in todo}
    # The LLM call takes minutes: update the file as it is *now*, so drafts appended meanwhile survive.
    fresh = read_jsonl(drafts_path(v))
    n = 0
    for d in fresh:
        if d.get("id") not in todo_ids:
            continue
        d["judged"] = True  # never send the same draft twice, even if the LLM skipped it
        r = by_id.get(d["id"])
        if not r:
            continue
        n += 1
        if not r.get("keep"):
            d["status"] = "noise"
            continue
        d["rule"] = short(str(r.get("rule", "")), 220)
        d["suggest"] = {k: r.get(k) for k in ("mechanism", "words", "tools", "commands", "class")}
    write_jsonl(drafts_path(v), fresh)
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
    group_ids = next((
        {x["id"] for x in g} for g in groups([x for x in all_d if x.get("status") == "open"])
        if any(x["id"] == draft_id for x in g)), {draft_id})
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
    for x in all_d:
        if x["id"] in group_ids:  # the whole group of similar corrections is answered by this lesson
            x["status"] = "approved"
            x["lesson"] = lesson.id
    write_jsonl(drafts_path(v), all_d)
    return lesson


def reject(v: Vault, draft_id: str, block_words: list[str] | None = None, reason: str = "") -> bool:
    all_d = read_jsonl(drafts_path(v))
    ids = next(({x["id"] for x in g} for g in groups([x for x in all_d if x.get("status") == "open"])
                if any(x["id"] == draft_id for x in g)), {draft_id})
    ok = False
    for d in all_d:
        if d["id"] in ids:
            d["status"] = "rejected"
            ok = True
    write_jsonl(drafts_path(v), all_d)
    if ok and block_words:
        append_jsonl(v.path("rejected.jsonl"), {"t": now_iso(), "words": block_words, "reason": reason})
    return ok
