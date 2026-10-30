"""Knowledge from your sessions: what you decided, found out or settled, filed into the notes at night.

The nightly run reads what is new in your sessions — your messages and the agent's answers, never raw tool
output — and asks the LLM for the few things worth knowing next time: decisions, facts about your projects,
pitfalls, preferences. Each one lands in the inbox as one self-contained sentence; `distill` then files it
into the note that owns the topic, where a newer statement replaces an outdated one.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import events, inbox, learn, llm
from .config import Vault
from .transcripts import parse
from .util import load_json, save_json, short

PROMPT = llm.MARKER + """
You keep the long-term memory of a developer's coding agent. Below are condensed sessions: the user's messages
and the agent's answers. Extract only what will matter in a FUTURE session and cannot be read from the code:
- decision: a choice that was made, and why
- fact: a lasting fact about the user's projects, systems or setup (where something runs, which version, who owns what)
- pitfall: something that went wrong, and how to avoid it next time
- preference: how the user wants things done
At most @MAX@ items per session; fewer is better and none is fine. One sentence each that makes sense on its own
(name the project or system). Never secrets, tokens, passwords or personal data; never task state that will be
stale tomorrow ("the tests pass now"). Write in the language of the session.

Return a JSON list: [{"session": 1, "type": "decision", "claim": "...", "quote": "<the user's words, if they said it>"}]

Sessions:
@SESSIONS@
"""
TYPES = ("decision", "fact", "pitfall", "preference")


def condense(turns, limit: int = 6000) -> str:
    """The user's messages and the agent's answers, without tool output; the end weighs more than the start."""
    parts, last = [], ""
    for t in turns:
        if t.role == "assistant":
            last = t.text
        elif t.role == "user":
            if last:
                parts.append("agent: " + short(last, 300))
            parts.append("user: " + short(t.text, 500))
            last = ""
    if last:
        parts.append("agent: " + short(last, 700))  # the final answer often sums the session up
    text = "\n".join(parts)
    if len(text) <= limit:
        return text
    head = text[: limit // 4]
    tail = text[-(limit - len(head) - 20):]
    return head.rsplit("\n", 1)[0] + "\n[…]\n" + tail.split("\n", 1)[-1]


def _todo(v: Vault, state: dict, max_sessions: int) -> list[tuple[Path, object, int]]:
    """(file, transcript, first new turn) for sessions with something new, newest first."""
    out = []
    cutoff = time.time() - 4 * 86400
    for f in reversed(learn.transcript_files(v)):
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            break  # sorted by mtime: everything after this is older
        done = state.get(str(f), {})
        if done.get("sig") == f"{st.st_mtime:.0f}:{st.st_size}":
            continue
        tr = parse(f)
        if tr is None or tr.internal:
            continue
        start = int(done.get("turns") or 0)
        if sum(1 for t in tr.turns[start:] if t.role == "user") < 3:
            continue  # too little new to be worth a call; picked up once there is more
        out.append((f, tr, start))
        if len(out) >= max_sessions:
            break
    return out


def run(v: Vault, budget_chars: int = 24000) -> dict:
    """Read what is new in recent sessions and file the claims in the inbox. Returns counts."""
    state = load_json(v.path("state", "extract.json"), {})
    state = state if isinstance(state, dict) else {}
    todo = _todo(v, state, int(v.get("extract_max_sessions")))
    out = {"sessions": 0, "claims": 0}
    while todo:
        batch, size = [], 0
        while todo and (not batch or size + 6000 <= budget_chars):
            f, tr, start = todo.pop(0)
            text = condense(tr.turns[max(0, start - 2):])
            repo = Path(tr.cwd).name if tr.cwd else ""
            batch.append((f, tr, f"[{len(batch) + 1}]" + (f" repo: {repo}" if repo else "") + "\n" + text))
            size += len(text)
        prompt = PROMPT.replace("@MAX@", "5").replace("@SESSIONS@", "\n\n".join(b[2] for b in batch))
        try:
            res = llm.ask_json(v, prompt)
        except (llm.NoLLM, ValueError, OSError) as e:
            events.log(v, "error", where="extract", msg=str(e)[:200])
            break
        for r in res if isinstance(res, list) else []:
            if not isinstance(r, dict) or str(r.get("type")) not in TYPES:
                continue
            try:
                n = int(r.get("session") or 0)
                _, tr, _ = batch[n - 1]
            except (ValueError, IndexError):
                continue
            try:
                inbox.add(v, str(r["type"]), str(r.get("claim") or ""), quote=short(str(r.get("quote") or ""), 300),
                          source="extract", session=tr.session)
                out["claims"] += 1
            except (inbox.Rejected, KeyError):
                pass
        for f, tr, _ in batch:
            st = f.stat()
            state[str(f)] = {"sig": f"{st.st_mtime:.0f}:{st.st_size}", "turns": len(tr.turns)}
            out["sessions"] += 1
    keep = {str(f) for f in learn.transcript_files(v)}
    save_json(v.path("state", "extract.json"), {k: x for k, x in state.items() if k in keep})
    return out
