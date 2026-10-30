"""Hook handlers. Native for Claude Code (JSON on stdin, JSON on stdout).

Every handler is fail-safe: any exception -> empty output, the agent is never blocked by a
broken brain. Budgets are small on purpose: recall injects at most 2 notes as one line each,
lessons fire once per session.

  session-start   always-lessons, pending drafts/inbox, MUST lint findings, nightly report
  prompt          recall (learned + graph), word-triggered lessons
  pre-tool        secret block on writes, guard lessons (ask), reflex lessons on tools/commands/paths
  post-tool       learning signal (which notes the agent actually opened) + lint of edited notes
  session-end     learn from this session's transcript in the background
  pre-compact     write a handoff from the transcript; session-start (source=compact) injects it again
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import cache, events, handoff, inbox, learn, trust
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
    always = [l for l in trust.effective(v, lib.lessons()) if l.mechanism == "always"][: int(v.get("always_max"))]
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
    source = str(payload.get("source") or "")
    h = handoff.load(v, session) if source == "compact" else None
    if h:
        lines.append("BRAIN-HANDOFF (written from the transcript right before the context was compacted; the user's words "
                     "are verbatim and win over the summary):\n" + handoff.render(h))
    elif source in ("", "startup", "clear"):
        prev = handoff.latest(v, repo=repo, exclude=session, max_age_h=48) if repo else None
        if prev:
            lines.append(f"BRAIN-HANDOFF: the last session in {repo} ({prev.get('agent')}, {str(prev.get('t', ''))[:16]}) "
                         f"left a handoff — if the user wants to continue that work, run `{b} resume`.")
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
    silent = trust.untrusted(v, lib.lessons())
    if silent:
        pending.append(f"{len(silent)} lessons stay silent until the user checks them (written by an agent or never approved) → `{b} trust`")
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
    for l in trust.effective(v, lib.lessons()):
        if l.mechanism in ("reflex", "skill") and words_match(l, text) and _mark_lesson(v, session, st, l, "words"):
            lines.append(f"LESSON {l.id}: {l.rule}")
    events.save_session_state(v, session, st)
    return _out("UserPromptSubmit", "\n".join(lines)) if lines else {}


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# approving or trusting a rule is the user's call: an agent running these commands gets a permission prompt
BRAIN_APPROVAL = re.compile(r"(?:\bbrain(?:\.py|\.exe)?|self_improving_brain)[\\\"']*\s+(?:--vault\s+\S+\s+)?"
                            r"(approve|trust|promote|pack\s+add|team\s+(?:add|propose)|skills\s+approve)\b")


def _paths_in(tool_input: dict) -> list[str]:
    out = [str(tool_input[k]) for k in ("file_path", "path", "notebook_path") if tool_input.get(k)]
    return out


def written_text(tool_input: dict) -> str:
    """What a Write/Edit/MultiEdit/NotebookEdit call puts into a file."""
    content = str(tool_input.get("content") or tool_input.get("new_string") or tool_input.get("new_source") or "")
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict):
            content += "\n" + str(e.get("new_string", ""))
    return content


def _in(p: str, root: Path) -> bool:
    try:
        return Path(p).expanduser().resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def check_hits(lessons, tool: str, cmd: str, paths: list[str], content: str) -> list[Lesson]:
    """Lessons whose `check:` matches what the agent writes or runs."""
    out = []
    for l in lessons:
        if not l.check:
            continue
        if tool in WRITE_TOOLS:
            if l.paths and not any(regex_match(l.paths, p) for p in paths):
                continue
            if regex_match(l.check, content):
                out.append(l)
        elif cmd and regex_match(l.check, cmd):
            out.append(l)
    return out


def guard_for(lessons, tool: str, cmd: str, paths: list[str], content: str = ""):
    """(first guard lesson that fires, reflex lessons whose triggers fire) for one proposed action."""
    reflexes = []
    for l in lessons:
        fired = (tool_match(l, tool) or regex_match(l.commands, cmd)
                 or any(regex_match(l.paths, p) for p in paths) or (cmd and regex_match(l.paths, cmd)))
        if l.mechanism == "guard":
            if fired or (l.check and check_hits([l], tool, cmd, paths, content)):
                return l, reflexes
        elif fired and l.mechanism in ("reflex", "skill"):
            reflexes.append(l)
    return None, reflexes


def pre_tool(v: Vault, payload: dict) -> dict:
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    ti = ti if isinstance(ti, dict) else {}
    session = _session(payload)
    paths = _paths_in(ti)
    content = written_text(ti) if tool in WRITE_TOOLS else ""
    if tool in WRITE_TOOLS and any(_in(p, v.meta) for p in paths):
        return _out("PreToolUse", decision={
            "permissionDecision": "deny",
            "permissionDecisionReason": "self-improving-brain: .brain/ holds the brain's own state (trust ledger, events)."
            " Change it through the brain CLI, never by editing files."})
    if tool in WRITE_TOOLS and any(_in(p, v.root) for p in paths):
        found = scan(content)
        if found:
            return _out("PreToolUse", decision={
                "permissionDecision": "deny",
                "permissionDecisionReason": "self-improving-brain: refusing to write a secret into the vault ("
                + ", ".join(k for k, _ in found) + "). Store the variable name and where the value lives instead."})
    cmd = command_head(str(ti.get("command") or "")) if tool == "Bash" else ""
    if cmd and v.get("guard_brain_commands") and BRAIN_APPROVAL.search(cmd):
        return _out("PreToolUse", decision={
            "permissionDecision": "ask",
            "permissionDecisionReason": "self-improving-brain: approving or trusting a rule needs the user's own OK."})
    candidates = [l for l in trust.effective(v, cache.lessons(v))
                  if l.tools or (cmd and (l.commands or l.paths or l.check)) or (paths and (l.paths or l.check))]
    if not candidates:
        return {}
    st = events.session_state(v, session)
    guard, reflexes = guard_for(candidates, tool, cmd, paths, content)
    lines = []
    for l in reflexes:
        if _mark_lesson(v, session, st, l, "tool:" + tool):
            lines.append(f"LESSON {l.id}: {l.rule}")
    if guard:
        events.log(v, "lesson", session=session, id=guard.id, trigger="guard:" + tool)
    events.save_session_state(v, session, st)
    if guard:
        return _out("PreToolUse", "\n".join(lines), {
            "permissionDecision": "ask",
            "permissionDecisionReason": f"self-improving-brain lesson {guard.id}: {guard.rule}"})
    return _out("PreToolUse", "\n".join(lines)) if lines else {}


# matches `brain read x`, `python3 "/path/brain" read x` and `python -m self_improving_brain read x`
READ_CMD = re.compile(r"(?:\bbrain(?:\.py|\.exe)?|self_improving_brain)[\"']?\s+(?:--vault\s+\S+\s+)?read\s+[\"']?([\w\-./]+)")


def _touches_vault(v: Vault, payload: dict, command: str) -> bool:
    cwd = str(payload.get("cwd") or "")
    home = str(Path.home())
    root = str(v.root)
    return bool((cwd and _in(cwd, v.root)) or root in command
                or (root.startswith(home) and "~" + root[len(home):] in command))


def _rule_checks(v: Vault, session: str, tool: str, cmd: str, paths: list[str], content: str) -> list[str]:
    """Rules that check themselves: a lesson's `check:` matched what the agent just did."""
    lessons = [l for l in trust.effective(v, cache.lessons(v)) if l.check and l.mechanism != "guard"]
    if not lessons or not (content or cmd):
        return []
    hits = check_hits(lessons, tool, cmd, paths, content)
    events.count_check(v, len(hits))
    lines = []
    for l in hits:
        events.log(v, "violation", session=session, id=l.id, tool=tool, sample=short(cmd or content, 120))
        lines.append(f"BRAIN-CHECK {l.id}: what you just did breaks this rule: {l.rule} "
                     "Fix it now, unless the user asked for exactly this.")
    return lines


def post_tool(v: Vault, payload: dict) -> dict:
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    ti = ti if isinstance(ti, dict) else {}
    session = _session(payload)
    paths = _paths_in(ti)
    lines: list[str] = []
    if tool == "Bash":
        full = str(ti.get("command") or "")
        m = READ_CMD.search(full)
        if m:
            n = cache.library(v).find(m.group(1))
            if n:
                events.log(v, "read", session=session, note=n.name, via="cli")
        if _touches_vault(v, payload, full):
            trust.mark_pending(v, cache.lessons(v), session, "Bash")
        lines += _rule_checks(v, session, tool, command_head(full), [], "")
        return _out("PostToolUse", "\n".join(lines)) if lines else {}
    in_vault = [p for p in paths if p.endswith(".md") and _in(p, v.root)]
    if tool == "Read":
        lib = cache.library(v)
        for p in in_vault:
            n = lib.find(Path(p).stem)
            if n:
                events.log(v, "read", session=session, note=n.name, via="read")
        return {}
    if tool not in WRITE_TOOLS:
        return {}
    edited, lib = [], None
    if in_vault:
        # first, before anything else looks at the lessons: what this call wrote is the agent's, not yours
        lib = Library(v.root)
        edited = [n for n in (lib.find(Path(p).stem) for p in in_vault) if n]
        silent = trust.mark_pending(v, [l for n in edited for l in n.lessons], session, tool)
        if silent:
            lines.append(f"BRAIN: you wrote the lesson(s) {', '.join(silent)}. They stay silent until the user checks them "
                         f"with `{cli_name()} trust` — tell the user.")
    lines += _rule_checks(v, session, tool, "", paths, written_text(ti))
    found = [str(f) for n in edited for f in lint_note(n, lib, int(v.get("stale_days")))[:5]]
    if found:
        lines += ["BRAIN-LINT on the note you just edited:"] + found
    return _out("PostToolUse", "\n".join(lines)) if lines else {}


def _tail_turns(path: Path, max_bytes: int = 400_000):
    """Parse only the end of a (possibly huge) transcript — Stop fires after every answer."""
    from .transcripts import read_part
    tr = read_part(path, max_bytes)
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


def pre_compact(v: Vault, payload: dict) -> dict:
    tp = payload.get("transcript_path")
    if tp and Path(str(tp)).is_file():
        handoff.from_transcript(v, Path(str(tp)), str(payload.get("cwd") or ""))
    return {}


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
    "pre-compact": pre_compact,
}


def run(event: str, vault_arg: str | None = None, plugin: bool = False) -> int:
    if os.environ.get("BRAIN_HOOKS_OFF"):
        return 0
    if plugin:
        from .adapters import claude_hooks_installed
        if claude_hooks_installed():
            return 0  # `brain install claude` hooks are there too: one brain per event, not two
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
        if plugin and event == "session-start":
            b = cli_name()
            sys.stdout.write(json.dumps(_out("SessionStart",
                "self-improving-brain (plugin) has no vault yet. If the user wants it, run "
                f"`{b} setup --yes --no-github --no-schedule` (creates ~/brain and learns from their past sessions; "
                f"backup and the nightly run can be added later with `{b} setup` in a terminal)."), ensure_ascii=True))
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
