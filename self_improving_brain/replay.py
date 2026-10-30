"""Replay your past sessions against today's lessons: which corrections would they have caught?

For every correction in your transcripts (the detection `brain learn` uses) replay looks at the
moment before it: the prompt that started the turn and every tool call the agent made until you
corrected it. A lesson *catches* a correction when it is about that mistake and its trigger fires in
that window: a guard would have asked first, a reflex or an always-lesson would have been in context.

  caught     a matching lesson would have fired before the mistake
  missed     a matching lesson exists, but none of its triggers fired → widen its words or commands
  uncovered  no lesson is about it yet → `--draft` turns these into lesson drafts

No LLM, no agent run, nothing leaves this machine: it reads the transcripts you already have.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

from . import cache, learn, trust
from .config import Vault
from .hooks import WRITE_TOOLS, check_hits, command_head, regex_match, tool_match, words_match
from .notes import Lesson
from .transcripts import parse
from .util import append_jsonl, digest, load_json, now_iso, save_json, short, terms

SHELL_TOOLS = {"Bash", "shell", "local_shell_call", "exec_command", "shell_command"}
RELEVANT = 0.4
_FIELD = re.compile(r'"(command|cmd|file_path|notebook_path|path)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _action(t) -> tuple[str, str, list[str], str]:
    """(tool, command, paths, written text) of one tool call; tolerant of transcripts cut at 2000 chars."""
    try:
        d = json.loads(t.text)
    except (json.JSONDecodeError, TypeError):
        d = {k: v.encode().decode("unicode_escape", "ignore") for k, v in _FIELD.findall(t.text or "")}
    if not isinstance(d, dict):
        d = {}
    tool = "Bash" if t.tool in SHELL_TOOLS else ("Edit" if t.tool == "apply_patch" else t.tool)
    cmd = d.get("command") or d.get("cmd") or ""
    if isinstance(cmd, list):
        cmd = " ".join(map(str, cmd[2:] if cmd[:2] in (["bash", "-lc"], ["sh", "-c"]) else cmd))
    paths = [str(d[k]) for k in ("file_path", "notebook_path", "path") if d.get(k)]
    if t.tool == "apply_patch":
        paths += re.findall(r"\*\*\* (?:Update|Add) File: (\S+)", t.text or "")
    text = "\n".join(str(d.get(k) or "") for k in ("content", "new_string", "new_source", "input"))
    return tool, command_head(str(cmd)), paths, text


def _fires(l: Lesson, prompt: str, actions: list) -> str:
    """How the lesson would have reached the agent in this window ("" = it would not have)."""
    if l.mechanism == "always":
        return "always"
    if l.mechanism in ("reflex", "skill") and words_match(l, prompt):
        return "words"
    for tool, cmd, paths, text in actions:
        if (tool_match(l, tool) or regex_match(l.commands, cmd) or any(regex_match(l.paths, p) for p in paths)
                or (cmd and regex_match(l.paths, cmd))):
            return "ask" if l.mechanism == "guard" else "tool"
        if l.check and check_hits([l], tool, cmd, paths, text):
            return "ask" if l.mechanism == "guard" else "check"
    return ""


def _about(l: Lesson, quote: str, actions: list) -> bool:
    """Is the lesson about this mistake? Its words, its command or its check match what happened."""
    if words_match(l, quote):
        return True
    for tool, cmd, paths, text in actions[-2:]:
        if regex_match(l.commands, cmd) or (l.check and check_hits([l], tool, cmd, paths, text)):
            return True
    return learn.similarity(l.rule + " " + " ".join(l.words), quote) >= RELEVANT


def _corrections(tr):
    """(index, quote, prompt before it, tool calls in between) for every correction in one transcript."""
    counted: set[int] = set()
    prompt, actions = "", []
    for i, t in enumerate(tr.turns):
        if t.role == "tool_call":
            actions.append(_action(t))
            continue
        c = learn.classify(tr.turns, i)
        if c and c[0] == "lesson" and c[1] >= learn.DRAFT_MIN:
            j = next((k for k in range(i, min(i + 4, len(tr.turns))) if tr.turns[k].role == "user"), i)
            if i not in counted and j not in counted:
                counted.update({i, j})
                yield i, c[2], c[1], prompt, list(actions)
        if t.role == "user":
            prompt, actions = t.text, []


def run(v: Vault, days: int = 30, files: list[Path] | None = None, draft: bool = False) -> dict:
    lessons = [l for l in cache.lessons(v)]
    silent = {l.id for l, _ in trust.untrusted(v, lessons)}
    lessons = [l for l in lessons if l.id not in silent]
    cutoff = time.time() - days * 86400
    paths = files if files is not None else [f for f in learn.transcript_files(v) if f.stat().st_mtime >= cutoff]
    known = {d.get("quote", "")[:200] for d in learn.drafts(v, None)}
    per = {l.id: Counter() for l in lessons}
    words_seen: dict[str, Counter] = {l.id: Counter() for l in lessons}
    rows, sessions, drafted = [], 0, 0
    for f in paths:
        tr = parse(f)
        if tr is None or tr.internal or not tr.turns:
            continue
        sessions += 1
        for i, quote, score, prompt, actions in _corrections(tr):
            about = [l for l in lessons if _about(l, quote, actions)]
            fired = [(l, how) for l in about for how in [_fires(l, prompt, actions)] if how]
            status = "caught" if fired else ("missed" if about else "uncovered")
            t = tr.turns[i]
            ctx = learn._context_before(tr.turns, i)
            rows.append({"session": tr.session, "agent": tr.source, "ts": t.ts, "quote": short(quote, 300),
                         "context": ctx, "status": status, "score": score,
                         "lessons": [l.id for l in about], "fired": [{"id": l.id, "how": how} for l, how in fired],
                         "before_lesson": bool(about) and all((t.ts or "")[:10] < (l.since or "9999") for l in about)})
            for l, how in fired:
                per[l.id]["caught"] += 1
                per[l.id]["ask" if how == "ask" else "shown"] += 1
            if status == "missed":
                for l in about:
                    per[l.id]["missed"] += 1
                    words_seen[l.id].update(w for w in terms(prompt) if w not in {x.lower() for x in l.words})
            if draft and status == "uncovered" and short(quote, 500)[:200] not in known:
                key = digest(str(tr.session), quote[:300], str(t.ts or ""))
                append_jsonl(learn.drafts_path(v), {
                    "id": "d-" + key[:8], "t": now_iso(), "kind": "lesson", "status": "open", "score": score,
                    "quote": short(quote, 500), "context": ctx, "session": tr.session, "source": tr.source,
                    "ts": t.ts, "rule": "", "via": "replay"})
                known.add(short(quote, 500)[:200])
                drafted += 1
    n = len(rows)
    by = Counter(r["status"] for r in rows)
    out = {
        "t": now_iso(), "days": days, "sessions": sessions, "lessons": len(lessons), "silent_lessons": len(silent),
        "corrections": n, "caught": by["caught"], "missed": by["missed"], "uncovered": by["uncovered"],
        "would_ask": sum(1 for r in rows if any(x["how"] == "ask" for x in r["fired"])),
        "caught_pct": round(100 * by["caught"] / n) if n else None, "drafted": drafted,
        "per_lesson": [{"id": lid, **c, "widen": [w for w, _ in words_seen[lid].most_common(3)]}
                       for lid, c in sorted(per.items(), key=lambda x: -(x[1]["caught"] + x[1]["missed"])) if c],
        "rows": rows[-400:],
    }
    save_json(v.path("state", "replay.json"), out)
    return out


def last(v: Vault) -> dict | None:
    r = load_json(v.path("state", "replay.json"), None)
    return r if isinstance(r, dict) else None


def render(r: dict, show: int = 6) -> str:
    if not r["corrections"]:
        return (f"REPLAY ({r['days']} days, {r['sessions']} sessions): no corrections found — "
                "nothing to replay yet, or `transcripts` in .brain/config.json points nowhere")
    out = [f"REPLAY · {r['sessions']} sessions · last {r['days']} days · {r['lessons']} active lessons",
           f"  Your lessons would have caught {r['caught']} of {r['corrections']} corrections ({r['caught_pct']} %)"
           + (f" — {r['would_ask']} of them by asking you first." if r["would_ask"] else "."),
           "  (caught = a lesson about this mistake would have fired before it happened)",
           f"  missed {r['missed']} (a lesson exists, its triggers did not fire) · uncovered {r['uncovered']} (no lesson yet)"]
    if r.get("silent_lessons"):
        out.append(f"  {r['silent_lessons']} lessons are silent until you trust them (`brain trust`) and were not counted.")
    if r["per_lesson"]:
        out.append("LESSONS")
        for p in r["per_lesson"][:12]:
            tip = f"  → widen: words {', '.join(p['widen'])}" if p.get("missed") and p.get("widen") else ""
            out.append(f"  {p['id']:40} caught {p.get('caught', 0):3} · missed {p.get('missed', 0):3}{tip}")
    for status, title in (("missed", "MISSED (the lesson was there, the trigger was not)"),
                          ("uncovered", "UNCOVERED (no lesson yet → `brain replay --draft`)")):
        rows = [x for x in r["rows"] if x["status"] == status][-show:]
        if rows:
            out.append(title)
            out += [f"  {str(x['ts'])[:10]} {x['agent']:6} “{short(x['quote'], 110)}”" for x in rows]
    if r.get("drafted"):
        out.append(f"✓ {r['drafted']} new lesson drafts — review them with `brain review`")
    return "\n".join(out)
