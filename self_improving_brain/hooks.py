"""Hook handlers. Native for Claude Code (JSON on stdin, JSON on stdout).

Every handler is fail-safe: any exception -> empty output, the agent is never blocked by a
broken brain. Budgets are small on purpose: recall injects at most 2 notes as one line each,
lessons fire once per session.

  session-start   always-lessons, pending drafts/inbox, MUST lint findings, nightly report
  prompt          recall (learned + graph), word-triggered lessons
  pre-tool        secret block on writes, guard lessons (ask), reflex lessons on tools/commands/paths
  post-tool       learning signal (which notes the agent actually opened) + lint of edited notes
  session-end     learn from this session's transcript in the background
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import cache, events, inbox, learn
from .adapters import cli_name
from .config import NotAVault, Vault, open_vault
from .lint import lint_note
from .notes import Library, Lesson
from .search import learned_model
from .secrets import scan
from .transcripts import HOOK_MARK
from .util import fold, load_json, short, terms

# The Stop hook interrupts the agent, so only unmistakable signals count there ("why did you…",
# "I told you…", "from now on always…"). Everything weaker waits quietly for `brain review`.
STOP_MIN = 0.8


def _out(event: str, context: str = "", decision: dict | None = None) -> dict:
    hso: dict = {"hookEventName": event}
    if context:
        hso["additionalContext"] = context
    if decision:
        hso.update(decision)
    return {"hookSpecificOutput": hso} if (context or decision) else {}


def _session(payload: dict) -> str:
    return str(payload.get("session_id") or payload.get("sessionId") or "nosession")


def _mark_lesson(v: Vault, session: str, state: dict, lesson: Lesson, trigger: str) -> bool:
    if lesson.id in state["lessons"]:
        return False
    state["lessons"].append(lesson.id)
    events.log(v, "lesson", session=session, id=lesson.id, trigger=trigger)
    return True


# --- lesson trigger matching --------------------------------------------------------------

def words_match(lesson: Lesson, text: str) -> bool:
    """Two keyword hits, or one multi-word phrase hit. A single common word never fires."""
    if not lesson.words:
        return False
    low = text.lower()
    t = set(terms(text))
    hits = 0
    for w in lesson.words:
        wl = w.lower().strip()
        if " " in wl and wl in low:
            return True
        if all(x in t for x in terms(wl)) and terms(wl):
            hits += 1
    return hits >= 2


def tool_match(lesson: Lesson, tool: str) -> bool:
    return any(tool == t or tool.endswith("__" + t) for t in lesson.tools)


def command_head(cmd: str) -> str:
    """First line, heredoc bodies stripped: writing a lesson via heredoc must not trigger it."""
    return (cmd.split("<<", 1)[0].splitlines() or [""])[0] if cmd else ""


def regex_match(rx: str, text: str) -> bool:
    if not rx or not text:
        return False
    try:
        return re.search(rx, text) is not None
    except re.error:
        return False


# --- handlers -----------------------------------------------------------------------------

def repo_of(cwd: str) -> str:
    """Name of the git repo (or folder) the agent works in."""
    if not cwd:
        return ""
    p = Path(cwd)
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d.name
    return p.name if p != Path.home() else ""


def _key(s) -> str:
    """Comparable form of a repo, tag or note name; names without Latin letters compare as written."""
    s = str(s or "").strip()
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-") or s.casefold()


def repo_notes(lib: Library, repo: str, k: int = 3) -> list:
    """Notes that declare `repo: <name>`, are tagged with it, or are named after it."""
    r = _key(repo)
    if not r:
        return []
    hits = []
    for n in lib.notes.values():
        raw = n.meta.get("repo")
        declared = {_key(x) for x in (raw if isinstance(raw, list) else [raw])} - {""}
        name = _key(n.name)
        if r in declared or r in {_key(x) for x in n.tags} or name == r or name.startswith(r + "-"):
            hits.append(n)
    hits.sort(key=lambda n: (not n.is_hub, n.name))
    return hits[:k]


def session_start(v: Vault, payload: dict) -> dict:
    lib = cache.library(v)
    events.prune_sessions(v)
    session = _session(payload)
    st = events.session_state(v, session)
    tp = payload.get("transcript_path")
    if "checked_ts" not in st and tp and Path(str(tp)).is_file():
        # a resumed session starts with old turns: the knowledge check only looks at what comes next
        st["checked_ts"] = max((t.ts for t in _tail_turns(Path(str(tp))) if t.ts), default="")
    always = [l for l in lib.lessons() if l.mechanism == "always"][: int(v.get("always_max"))]
    b = cli_name()
    lines = [f"SELF-IMPROVING-BRAIN vault: {v.root} — before answering from memory, run `{b} search \"<question>\"`;"
             f" open a note with `{b} read <note> [section]`; store new knowledge with"
             f" `{b} remember <fact|decision|pitfall|preference|todo> \"<one sentence>\"`."]
    if always:
        lines.append("LESSONS (apply in every session):")
        lines += [f"- {l.id}: {l.rule}" for l in always]
        for l in always:
            _mark_lesson(v, session, st, l, "always")
    repo = repo_of(str(payload.get("cwd") or os.getcwd()))
    rnotes = repo_notes(lib, repo)
    if rnotes:
        st["shown"] += [n.name for n in rnotes]
        lines.append(f"BRAIN-REPO {repo}: " + " · ".join(f"{n.name} — {short(n.description, 100)}" for n in rnotes))
    events.save_session_state(v, session, st)
    pending = []
    groups = learn.groups(learn.drafts(v))
    if groups:
        pending.append(f"{len(groups)} lesson candidates from past sessions → `{b} review` (or /brain-review)")
    i = len(inbox.items(v))
    if i:
        pending.append(f"{i} inbox items → `brain distill`")
    report = load_json(v.path("state", "nightly.json"), {})
    for msg in (report.get("attention") or [])[:3]:
        pending.append(msg)
    for f in cache.must_findings(v)[:3]:
        pending.append("fix " + " ".join(f.split()))
    if pending:
        lines.append("BRAIN TODO (mention to the user only if relevant): " + " · ".join(pending))
    return _out("SessionStart", "\n".join(lines))


def prompt(v: Vault, payload: dict) -> dict:
    text = str(payload.get("prompt") or "")
    session = _session(payload)
    if not text.strip() or text.lstrip().startswith("/"):
        return {}
    idx = cache.index(v, learned_model(v))
    lib = idx.lib
    st = events.session_state(v, session)
    hits = idx.score(text, graph_beta=float(v.get("graph_beta")), seeds=int(v.get("graph_seeds")),
                     include_hubs=False)
    th = float(v.get("recall_threshold"))
    chosen = []
    if hits and hits[0].note.name not in st["shown"]:
        for h in hits:
            if h.score < th or len(chosen) >= int(v.get("recall_max_notes")):
                break
            if h.note.name not in st["shown"]:
                chosen.append(h)
    q = list(dict.fromkeys(terms(text)))[:12]
    events.log(v, "recall", session=session, notes=[h.note.name for h in chosen], terms=q,
               scores=[h.score for h in chosen])
    lines = []
    if chosen:
        st["shown"] += [h.note.name for h in chosen]
        lines.append(f"BRAIN-RECALL (open with `{cli_name()} read <note>` if relevant): " + " · ".join(
            f"{h.note.name} — {short(h.note.description, 110)}" for h in chosen))
    for l in lib.lessons():
        if l.mechanism in ("reflex", "skill") and words_match(l, text) and _mark_lesson(v, session, st, l, "words"):
            lines.append(f"LESSON {l.id}: {l.rule}")
    events.save_session_state(v, session, st)
    return _out("UserPromptSubmit", "\n".join(lines)) if lines else {}


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _paths_in(tool_input: dict) -> list[str]:
    out = [str(tool_input[k]) for k in ("file_path", "path", "notebook_path") if tool_input.get(k)]
    return out


def pre_tool(v: Vault, payload: dict) -> dict:
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    ti = ti if isinstance(ti, dict) else {}
    session = _session(payload)
    paths = _paths_in(ti)
    if tool in WRITE_TOOLS and any(Path(p).resolve().is_relative_to(v.root) for p in paths):
        content = str(ti.get("content") or ti.get("new_string") or "")
        for e in ti.get("edits") or []:
            content += "\n" + str(e.get("new_string", ""))
        found = scan(content)
        if found:
            return _out("PreToolUse", decision={
                "permissionDecision": "deny",
                "permissionDecisionReason": "self-improving-brain: refusing to write a secret into the vault ("
                + ", ".join(k for k, _ in found) + "). Store the variable name and where the value lives instead."})
    cmd = command_head(str(ti.get("command") or "")) if tool == "Bash" else ""
    candidates = [l for l in cache.lessons(v) if l.tools or (cmd and (l.commands or l.paths)) or (paths and l.paths)]
    if not candidates:
        return {}
    st = events.session_state(v, session)
    lines, guard = [], None
    for l in candidates:
        fired = (tool_match(l, tool) or regex_match(l.commands, cmd)
                 or any(regex_match(l.paths, p) for p in paths) or (cmd and regex_match(l.paths, cmd)))
        if not fired:
            continue
        if l.mechanism == "guard":
            events.log(v, "lesson", session=session, id=l.id, trigger="guard:" + tool)
            guard = l
            break
        if l.mechanism in ("reflex", "skill") and _mark_lesson(v, session, st, l, "tool:" + tool):
            lines.append(f"LESSON {l.id}: {l.rule}")
    events.save_session_state(v, session, st)
    if guard:
        return _out("PreToolUse", "\n".join(lines), {
            "permissionDecision": "ask",
            "permissionDecisionReason": f"self-improving-brain lesson {guard.id}: {guard.rule}"})
    return _out("PreToolUse", "\n".join(lines)) if lines else {}


# matches `brain read x`, `python3 "/path/brain" read x` and `python -m self_improving_brain read x`
READ_CMD = re.compile(r"(?:\bbrain(?:\.py|\.exe)?|self_improving_brain)[\"']?\s+(?:--vault\s+\S+\s+)?read\s+[\"']?([\w\-./]+)")


def post_tool(v: Vault, payload: dict) -> dict:
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    ti = ti if isinstance(ti, dict) else {}
    session = _session(payload)
    paths = _paths_in(ti)
    lib = None
    if tool == "Bash":
        m = READ_CMD.search(str(ti.get("command") or ""))
        if m:
            lib = cache.library(v)
            n = lib.find(m.group(1))
            if n:
                events.log(v, "read", session=session, note=n.name, via="cli")
        return {}
    in_vault = [p for p in paths if p.endswith(".md") and Path(p).resolve().is_relative_to(v.root)]
    if not in_vault:
        return {}
    lib = cache.library(v) if tool == "Read" else Library(v.root)
    if tool == "Read":
        for p in in_vault:
            n = lib.find(Path(p).stem)
            if n:
                events.log(v, "read", session=session, note=n.name, via="read")
        return {}
    if tool in WRITE_TOOLS:
        msgs = []
        for p in in_vault:
            n = lib.find(Path(p).stem)
            if n:
                msgs += [str(f) for f in lint_note(n, lib, int(v.get("stale_days")))[:5]]
        if msgs:
            return _out("PostToolUse", "BRAIN-LINT on the note you just edited:\n" + "\n".join(msgs))
    return {}


def _tail_turns(path: Path, max_bytes: int = 400_000):
    """Parse only the end of a (possibly huge) transcript — Stop fires after every answer."""
    from .transcripts import parse_lines
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - max_bytes))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.split("\n")  # not splitlines(): JSON strings may hold a raw U+2028
    if size > max_bytes:
        lines = lines[1:]
    tr = parse_lines(lines, path)
    return tr.turns if tr and not tr.internal else []


SAVED = re.compile(r"(?:\bbrain(?:\.py|\.exe)?|self_improving_brain)[\\\"']*\s+(?:--vault\s+\S+\s+)?(remember|approve|lesson)\b")


def stop(v: Vault, payload: dict) -> dict:
    """Knowledge check at the end of an answer — only when the session gave a reason to keep something.

    A correction or a lasting preference that was not saved yet makes the agent pause once and decide:
    save it with `brain remember`, or finish. Never twice for the same moment, never in a loop.
    """
    if payload.get("stop_hook_active") or not v.get("capture_on_stop"):
        return {}
    tp = payload.get("transcript_path")
    if not tp or not Path(tp).exists():
        return {}
    session = _session(payload)
    st = events.session_state(v, session)
    turns = _tail_turns(Path(tp))
    last_checked = st.get("checked_ts", "")
    signal = None
    for i, t in enumerate(turns):
        if t.ts and t.ts <= last_checked:
            continue
        c = learn.classify(turns, i)
        if c and c[1] >= STOP_MIN:
            later = turns[i + 1:]
            if not any(x.role == "tool_call" and SAVED.search(x.text) for x in later):
                signal = c
    newest = max((t.ts for t in turns if t.ts), default=last_checked)
    st["checked_ts"] = newest
    events.save_session_state(v, session, st)
    if not signal:
        return {}
    kind, _, quote = signal
    b = cli_name()
    what = "a lasting preference" if kind == "preference" else "a correction"
    events.log(v, "capture", session=session, what=kind)
    return {"decision": "block", "reason": (
        f"{HOOK_MARK} BRAIN knowledge check: this session contains {what} that is not stored yet: “{short(quote, 160)}”. "
        f"If it will matter beyond this task, save it in one line — `{b} remember "
        f"{'preference' if kind == 'preference' else 'pitfall'} \"<one self-contained sentence>\" --quote \"<the user's words>\"` — "
        "then finish your answer. If it was a one-off, just finish. Do not mention this check to the user.")}


def session_end(v: Vault, payload: dict) -> dict:
    tp = payload.get("transcript_path")
    if tp and Path(tp).exists():
        args = [sys.executable, "-m", "self_improving_brain", "--vault", str(v.root), "learn", "--file", str(tp), "--quiet"]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent))
        except OSError:
            pass
    return {}


HANDLERS = {
    "session-start": session_start,
    "prompt": prompt,
    "pre-tool": pre_tool,
    "post-tool": post_tool,
    "session-end": session_end,
    "stop": stop,
}


def run(event: str, vault_arg: str | None = None) -> int:
    if os.environ.get("BRAIN_HOOKS_OFF"):
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:  # noqa: BLE001 — garbage on stdin must not break the agent
        payload = {}
    try:
        v = open_vault(vault_arg)
        out = HANDLERS[event](v, payload)
    except NotAVault:
        return 0
    except Exception as e:  # noqa: BLE001 — a hook must never break the agent
        if os.environ.get("BRAIN_DEBUG"):
            raise
        try:
            events.log(open_vault(vault_arg), "error", where=event, msg=str(e)[:200])
        except Exception:  # noqa: BLE001
            pass
        return 0
    if out:
        try:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
        except Exception:  # noqa: BLE001
            pass
    return 0
