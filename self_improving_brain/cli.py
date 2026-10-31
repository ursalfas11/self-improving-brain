"""`brain` — what you see is small on purpose: how it is doing, what it learned, undo.

Agents use more commands (search, read, remember, check, resume …). They work the same, they are just not
in your way: `brain help all` lists them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import adapters, distill, events, handoff, hooks, inbox, journal, learn, llm, maplib, metrics, nightly, replay, trust
from .config import DEFAULTS, MARKER, NotAVault, Vault, is_vault, open_vault, remember_vault
from .lint import lint
from .notes import Lesson, Library
from .search import Index, learned_model, snippet
from .secrets import scan
from .util import day_label, days_since, load_json, save_json, short, today

HELP = """brain — your agent learns from your corrections. Install it once, then forget it.

  brain              how it is doing
  brain review       what it learned lately, with undo
  brain undo <n>     take one change back
  brain ui           the whole brain in your browser
  brain setup        install: asks nothing (--github adds a private backup)

  brain doctor       check the installation
  brain update       update self-improving-brain
  brain uninstall    take it out of every agent again (your notes stay)

Agents use more commands (search, read, remember, check, resume …): brain help all"""


class _Paint:
    def __init__(self):
        self.on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def b(self, s: str) -> str:
        return self._c("1", s)

    def d(self, s: str) -> str:
        return self._c("2", s)

    def y(self, s: str) -> str:
        return self._c("33", s)

    def g(self, s: str) -> str:
        return self._c("32", s)


def _vault(a) -> Vault:
    return open_vault(a.vault)


def _ses() -> str:
    return os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("BRAIN_SESSION") or "cli"


def _log_read(v: Vault, note: str) -> None:
    # Inside Claude Code the post-tool hook logs this read with the real session id.
    # Log here only when we know the session; a "cli" session would skew recall precision.
    if _ses() != "cli":
        events.log(v, "read", session=_ses(), note=note, via="cli")


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s is not None else None


def _home(p: Path) -> str:
    h = str(Path.home())
    return "~" + str(p)[len(h):] if str(p).startswith(h + os.sep) else str(p)


# --- what you see ------------------------------------------------------------------------------

def c_status(a):
    try:
        v = _vault(a)
    except NotAVault:
        print("No brain on this machine yet. One command sets it up, and it asks nothing:\n\n    brain setup\n")
        return 0
    from . import cache
    c = _Paint()
    lessons = cache.lessons(v)
    lib = cache.library(v)
    d = metrics.week_digest(v)
    start = metrics.since_start(metrics.weekly_curve(v, 104))
    rep = load_json(v.path("state", "nightly.json"), {})
    sessions = len({e.get("session") for e in events.all_events(v, "turns")})
    rows = []
    if start:
        rows.append(("Mistakes", f"{start['now']:.1f} per 100 messages · "
                     f"{'−' if start['change'] <= 0 else '+'}{abs(start['change'])} % since {day_label(start['since'])}"))
    else:
        rows.append(("Mistakes", "measuring — the trend shows after a few weeks of sessions"))
    rows.append(("This week", metrics.learned_text(d)))
    cost = []
    if d["share"] is not None:
        cost.append(f"{100 * d['share']:.1f} % of your agent's tokens")
    if d["cost"]["calls"]:
        cost.append(f"${d['cost']['usd']:.2f} for the nights" if d["cost"]["usd"] else f"{d['cost']['calls']} LLM calls")
    rows.append(("Cost", " · ".join(cost) or "nothing measured yet"))
    rows.append(("Last night", nightly.summary(rep) if rep.get("t") else "not run yet"))
    print(f"\n  🧠  {c.b('self-improving-brain')}   {c.d(_home(v.root))}\n")
    for k, val in rows:
        print(f"  {c.d(f'{k:<12}')} {val}")
    always = sum(1 for l in lessons if l.mechanism == "always")
    guards = sum(1 for l in lessons if l.mechanism == "guard")
    n = lambda k, word: f"{k:,} {word}{'' if k == 1 else 's'}"
    print(c.d(f"\n  {n(len(lessons), 'rule')} ({always} always, {n(guards, 'guard')}) · {n(len(lib.notes), 'note')} · "
              f"{n(sessions, 'session')} read"))
    attention = []
    if not llm.available(v):
        attention.append("No LLM CLI: the brain measures, but cannot learn → install claude, codex or ollama, then brain setup")
    if not nightly.schedule_installed():
        attention.append("The nightly run is not scheduled on this machine → brain setup")
    elif rep.get("t") and (days_since(str(rep["t"])[:10]) or 0) > 2:
        attention.append(f"The nightly run last ran on {day_label(rep['t'])} → brain doctor")
    if llm.available(v) and rep.get("llm_cmd") is False:
        attention.append("Last night's run could not see your LLM setting, so it learned nothing → brain schedule install")
    attention += [x for x in rep.get("attention") or []][:3]
    silent = trust.untrusted(v, lessons)
    if silent:
        attention.append(f"{len(silent)} rule{'s were' if len(silent) != 1 else ' was'} written by an agent and stay"
                         f"{'s' if len(silent) == 1 else ''} silent until you look → brain review")
    if attention:
        print()
        for line in attention:
            print("  " + c.y("! ") + line)
    print(f"\n  {c.b('brain review')}   what it learned, with undo\n  {c.b('brain ui')}       the whole brain in your browser\n")
    return 0


def c_review(a):
    v = _vault(a)
    from . import cache
    c = _Paint()
    rows = journal.recent(v, days=a.days, undone=True)  # undone ones keep their number, so the others keep theirs
    silent = trust.untrusted(v, cache.lessons(v))
    if not rows and not silent:
        print(f"Nothing learned in the last {a.days} days. It keeps learning from your sessions every night.")
        return 0
    if rows:
        print(f"\nLearned in the last {a.days} days. Take anything back with {c.b('brain undo <n>')}.\n")
        for i, e in enumerate(rows, 1):
            what, text, why = journal.describe(e)
            day = day_label(e.get("t", ""))
            if e.get("status") == "undone":
                print(c.d(f"  {i:>2}  {day:<6}  {'undone':<15} {short(text, 110)}"))
                continue
            print(f"  {i:>2}  {c.d(f'{day:<6}')}  {c.b(f'{what:<15}')} {short(text, 110)}")
            if why:
                print(" " * 30 + c.d(short(why, 110)))
    if silent:
        print(f"\nWritten by an agent, silent until you OK them ({c.b('brain trust <id>')}):\n")
        for l, why in silent:
            print(f"  {l.id}  {short(l.rule, 100)}\n      {c.d(why)}")
    print()
    if rows and sys.stdin.isatty() and sys.stdout.isatty() and not a.no_input:
        try:
            ans = input("Undo which? (numbers, Enter keeps everything) ").replace(",", " ").split()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        return _undo(v, ans) if ans else 0
    return 0


def _undo(v: Vault, refs: list[str]) -> int:
    rc, ids = 0, []
    for ref in refs:  # numbers refer to the list as it is now: resolve all of them before changing anything
        e = journal.find(v, ref)
        if e is None:
            print(f"✗ nothing to undo for {ref!r} — `brain review` lists what can be undone")
            rc = 1
        else:
            ids.append(e["id"])
    for jid in ids:
        try:
            print("✓ " + journal.undo(v, jid))
        except (ValueError, KeyError, OSError) as e:
            print(f"✗ {str(e).strip(chr(39))}")
            rc = 1
    return rc


def c_undo(a):
    return _undo(_vault(a), a.refs)


def c_ui(a):
    from . import ui
    return ui.serve(None if a.demo else _vault(a), port=a.port, open_browser=not a.no_open)


def c_setup(a):
    from . import setup
    a.vault = a.setup_vault or a.vault
    return setup.run(a)


def c_update(a):
    root = Path(__file__).resolve().parent.parent
    if (root / ".git").exists():
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "-q"], capture_output=True, text=True)
        print("✓ updated" if r.returncode == 0 else "update failed: " + r.stderr.strip()[:300])
        return r.returncode
    print("installed as a package — update with:  pipx upgrade self-improving-brain")
    return 0


def c_doctor(a):
    ok = True
    try:
        v = _vault(a)
        print(f"✓ vault {v.root}")
    except NotAVault as e:
        print(f"✗ {e}")
        return 1
    lib = Library(v.root)
    print(f"✓ {len(lib.notes)} notes · {len(lib.lessons())} rules · {sum(len(n.links) for n in lib.notes.values())} links")
    claude = adapters.claude_hooks_installed()
    print(("✓" if claude else "·") + " Claude Code hooks " + ("installed" if claude else "not installed (brain install claude)"))
    for name, p in [("codex", Path.home() / ".codex" / "AGENTS.md"), ("gemini", Path.home() / ".gemini" / "GEMINI.md")]:
        on = p.exists() and adapters.BEGIN in p.read_text(encoding="utf-8")
        print(("✓ " if on else "· ") + f"{name} " + ("connected" if on else f"not connected (brain install {name})"))
    files = learn.transcript_files(v)
    print(f"{'✓' if files else '·'} {len(files)} transcripts visible for learning")
    if llm.available(v):
        exe = str(v.get("llm_cmd")).split()[0]
        s = llm.spent(v)
        print(f"✓ learns with {exe} · today {s['calls']} calls, ${s['usd']:.3f} of ${float(v.get('llm_budget_usd')):.2f}")
        if not shutil.which(exe):
            print(f"✗ {exe} is not on PATH")
            ok = False
    else:
        print("! no LLM CLI set — the brain measures but cannot learn (install claude, codex or ollama, then brain setup)")
    print(("✓" if nightly.schedule_installed() else "!") + " nightly run " +
          ("scheduled" if nightly.schedule_installed() else "not scheduled (brain setup)"))
    rep = load_json(v.path("state", "nightly.json"), {})
    if rep.get("t"):
        failed = [k for k, x in (rep.get("steps") or {}).items() if str(x).startswith("error:")]
        print(("✓" if not failed else "✗") + f" last nightly run {str(rep['t'])[:16]}"
              + (": " + "; ".join(f"{k} {str(rep['steps'][k])[7:120]}" for k in failed) if failed else ""))
        ok = ok and not failed
    else:
        print("· the nightly run has not run yet")
    url, problem = nightly.backup_state(v.root)
    if not url:
        print("· no backup remote — `brain setup --github` keeps a private copy on GitHub")
    elif problem:
        print(f"! backup remote {url}: {problem} (brain sync)")
    else:
        vis = nightly.remote_visibility(url)
        if vis == "PUBLIC":
            print(f"✗ backed up to {url} — but it is PUBLIC: anyone can read your brain. Make it private.")
            ok = False
        else:
            print(f"✓ backed up to {url}" + (" (private)" if vis == "PRIVATE" else ""))
    if url and not v.get("git_sync"):
        print("· nightly sync is off (brain config git_sync true)")
    ok = hook_selftest(v) and ok
    return 0 if ok else 1


def hook_selftest(v: Vault) -> bool:
    """Run every hook the way the agent does and time it."""
    import time
    probes = [("session-start", {"session_id": "doctor", "cwd": str(v.root)}),
              ("prompt", {"session_id": "doctor", "prompt": "how do lessons and recall work in this brain"}),
              ("pre-tool", {"session_id": "doctor", "tool_name": "Bash", "tool_input": {"command": "git status"}}),
              ("post-tool", {"session_id": "doctor", "tool_name": "Read", "tool_input": {"file_path": str(v.root / "INDEX.md")}})]
    env = dict(os.environ, BRAIN_VAULT=str(v.root))
    env.pop("BRAIN_HOOKS_OFF", None)
    good, times = True, []
    for event, payload in probes:
        t0 = time.perf_counter()
        r = subprocess.run(adapters.brain_cmd() + f" hook {event}", shell=True, input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        ms = (time.perf_counter() - t0) * 1000
        try:
            json.loads(r.stdout) if r.stdout.strip() else None
            valid = r.returncode == 0
        except json.JSONDecodeError:
            valid = False
        times.append(f"{event} {ms:.0f} ms")
        good = good and valid
    events.log(v, "doctor", session="doctor")
    print(("✓" if good else "✗") + " hooks answer correctly · " + " · ".join(times))
    return good


def c_install(a):
    v = _vault(a)
    for line in adapters.install(v, a.agent):
        print(line)
    return 0


def c_uninstall(a):
    if a.agent != "all":
        for line in adapters.uninstall(a.agent):
            print(line)
        return 0
    from . import mcp
    for agent in ("claude", "codex", "gemini"):
        for line in adapters.uninstall(agent):
            print(line)
    for c in mcp.CLIENTS:
        f = mcp.client_path(c)
        try:
            if f.exists() and ('"brain"' in (raw := f.read_text(encoding="utf-8")) or "[mcp_servers.brain]" in raw):
                print(f"{c}: {mcp.uninstall(c)}")
        except (ValueError, OSError) as e:
            print(f"{c}: {e}")
    try:
        print(nightly.schedule_remove(_vault(a)))
    except NotAVault:
        print(nightly.schedule_remove())
    try:
        print(f"Your notes stay where they are ({_home(_vault(a).root)}); delete the folder if you want them gone.")
    except NotAVault:
        pass
    return 0


# --- what agents use -------------------------------------------------------------------------------

def c_context(a):
    out = hooks.session_start(_vault(a), {"session_id": _ses()})
    print(out.get("hookSpecificOutput", {}).get("additionalContext", ""))
    return 0


def c_search(a):
    v = _vault(a)
    lib = Library(v.root)
    hits = Index(lib, learned_model(v)).score(a.query, graph_beta=float(v.get("graph_beta")))[: a.k]
    events.log(v, "tool", cmd="search", session=_ses())
    if a.json:
        print(json.dumps([{"note": h.note.name, "path": h.note.rel, "score": h.score, "section": h.section.title if h.section else "",
                           "updated": h.note.updated, "why": h.why} for h in hits], ensure_ascii=False, indent=1))
        return 0
    if not hits:
        print("no match — the brain does not know this yet. If you find out, `brain remember` it.")
        return 0
    for h in hits:
        sec = f" › {h.section.title}" if h.section and h.section.title != "(intro)" else ""
        upd = f" · updated {h.note.updated}" if h.note.updated else ""
        print(f"■ {h.note.name}{sec}  ({h.score}{upd}{' · ' + h.why if h.why != 'text' else ''})")
        snip = snippet(h.section, a.query)
        if snip:
            print("  " + snip.replace("\n", "\n  "))
    print(f"→ brain read <note> [\"<section>\"]")
    return 0


def c_read(a):
    v = _vault(a)
    lib = Library(v.root)
    n = lib.find(a.note)
    if n is None:
        print(f"no note {a.note!r}. Try: brain search \"{a.note}\"")
        return 1
    _log_read(v, n.name)
    if a.all:
        print(n.body)
        return 0
    if a.section:
        sec = None
        if a.section.isdigit() and 0 < int(a.section) <= len(n.sections):
            sec = n.sections[int(a.section) - 1]
        else:
            sec = next((s for s in n.sections if a.section.lower() in s.title.lower()), None)
        if sec is None:
            print(f"no section {a.section!r} in {n.name}")
            return 1
        print(f"{n.name} › {sec.title}{' · updated ' + n.updated if n.updated else ''}\n")
        print(sec.body)
        return 0
    print(f"{n.name} · {n.rel} · ~{n.tokens} tokens{' · updated ' + n.updated if n.updated else ''}")
    if n.description:
        print(f"{n.description}\n")
    if n.tokens <= 600:
        print(n.body)
        return 0
    intro = next((s for s in n.sections if s.title == "(intro)"), None)
    if intro:
        print(short(intro.body, 600) + "\n")
    print("Sections (~tokens):")
    for i, s in enumerate(n.sections, 1):
        if s.title != "(intro)":
            print(f" {i:2}. {s.title} · ~{s.tokens}")
    if n.lessons:
        print("Lessons here: " + ", ".join(l.id for l in n.lessons if not l.retired))
    print(f'→ brain read {n.name} "<section title or number>"   (or --all)')
    return 0


def c_neighbors(a):
    v = _vault(a)
    lib = Library(v.root)
    n = lib.find(a.note)
    if n is None:
        print(f"no note {a.note!r}")
        return 1
    g = lib.neighbors()
    for nb in sorted(g.get(n.name, ())):
        print(f"- {nb} — {lib.notes[nb].description[:100]}")
    return 0


def c_remember(a):
    v = _vault(a)
    try:
        rec = inbox.add(v, a.type, a.claim, quote=a.quote or "", note=a.note or "", source="cli", session=_ses())
    except inbox.Rejected as e:
        print(str(e))
        return 1
    print(f"✓ {rec['id']} in inbox ({rec['type']}). The nightly run files it into the right note.")
    return 0


def c_check(a):
    """For agents without hooks: would this action trip a guard lesson?"""
    v = _vault(a)
    from . import cache
    tool = a.tool or ("Bash" if a.command else "Write")
    cmd = hooks.command_head(a.command or "")
    lessons = trust.effective(v, cache.lessons(v))
    guard, reflexes = hooks.guard_for(lessons, tool, cmd, [a.path] if a.path else [], "")
    hits = hooks.check_hits([l for l in lessons if l.mechanism != "guard"], tool, cmd, [a.path] if a.path else [], "")
    for l in reflexes + [l for l in hits if l not in reflexes]:
        print(f"LESSON {l.id}: {l.rule}")
    if guard:
        events.log(v, "lesson", session=_ses(), id=guard.id, trigger="check:" + tool)
        print(f"GUARD {guard.id}: {guard.rule}\n→ ask the user before you do this.")
        return 3
    if not reflexes and not hits:
        print("no lesson applies")
    return 0


def c_resume(a):
    v = _vault(a)
    if a.list:
        for e in reversed(handoff.load_index(v)):
            print(f"{str(e.get('t', ''))[:16]}  {e.get('agent', '?'):6} {e.get('repo') or e.get('cwd') or '?':28} {e.get('session')}")
        return 0
    h = handoff.load(v, a.session) if a.session else None
    if h is None and not a.session:
        repo = "" if a.any else hooks.repo_of(os.getcwd())
        h = handoff.latest(v, repo=repo) or (handoff.latest(v) if repo and not a.any else None)
    if h is None:
        print("no handoff yet — one is written before every compaction (Claude Code) or with `brain handoff`")
        return 1
    print(handoff.render(h, budget=20000))
    return 0


def c_handoff(a):
    v = _vault(a)
    path = Path(a.file).expanduser() if a.file else handoff.newest_transcript(v, os.getcwd())
    if path is None or not path.is_file():
        print("no transcript found — pass --file <session.jsonl>")
        return 1
    h = handoff.from_transcript(v, path, os.getcwd())
    if h is None:
        print(f"nothing to hand off in {path}")
        return 1
    print(f"✓ handoff for session {h['session'][:12]} ({h['agent']}, {h.get('repo') or '?'}) — `brain resume` shows it")
    return 0


def c_mcp(a):
    from . import mcp
    if a.action == "serve":
        return mcp.serve(_vault(a))
    if a.action == "config":
        print(mcp.snippet(_vault(a), a.client or ""))
        return 0
    clients = mcp.detected() if a.client in (None, "all") else [a.client]
    if a.client and a.client not in (*mcp.CLIENTS, "all"):
        print(f"no automatic install for {a.client} — paste this into its MCP settings:\n{mcp.snippet(_vault(a), a.client)}")
        return 1
    if not clients:
        print("no MCP client found — `brain mcp config` prints the entry to paste into any client")
        return 1
    rc = 0
    for c in clients:
        try:
            print(f"{c:15} " + (mcp.install(_vault(a), c) if a.action == "install" else mcp.uninstall(c)))
        except (ValueError, OSError) as e:
            print(f"{c:15} ✗ {e}")
            rc = 1
    if a.action == "install":
        print("restart the client; it then has brain_context, brain_check, brain_correction, brain_handoff …")
    return rc


def c_hook(a):
    return hooks.run(a.event, a.vault, plugin=a.plugin)


# --- the machinery, for the curious and for scripts --------------------------------------------------

def c_init(a):
    root = Path(a.path or "~/brain").expanduser().resolve()
    if is_vault(root):
        print(f"{root} is already a self-improving-brain vault.")
        return 0
    if (root / ".brain").exists():
        print(f"refusing: {root}/.brain exists but is not a self-improving-brain vault. Pick another folder.")
        return 1
    if root.exists() and any(p for p in root.iterdir() if not p.name.startswith(".")) and not a.force:
        print(f"refusing: {root} is not empty. Use --force to add self-improving-brain to an existing folder of notes.")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    had_git = (root / ".git").exists()
    quiet = getattr(a, "quiet", False)
    tpl = Path(__file__).resolve().parent / "templates" / "vault"
    for src in tpl.rglob("*"):
        if src.is_dir():
            continue
        dst = root / src.relative_to(tpl)
        if dst.exists():
            if dst.name == ".gitignore":  # yours stays; only the brain's own lines are added
                have = dst.read_text(encoding="utf-8", errors="replace")
                add = [l for l in src.read_text(encoding="utf-8").splitlines()
                       if l.strip() and not l.startswith("#") and l not in have.split("\n")]
                if add:
                    with dst.open("a", encoding="utf-8") as fh:
                        fh.write(("" if have.endswith("\n") or not have else "\n") + "# self-improving-brain\n" + "\n".join(add) + "\n")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8").replace("{{TODAY}}", today()), encoding="utf-8")
    make_git = bool(a.git) and not had_git and shutil.which("git") is not None
    marker = {"version": DEFAULTS["version"], "created": today(), "git_owned": make_git}
    if had_git:
        # a repo you already had: the brain never commits your other files, and never pushes until you say so
        marker["git_sync"] = False
        if not quiet:
            print(f"note: {root} already is a git repo — nightly sync stays off until you run: brain config git_sync true")
    save_json(root / ".brain" / MARKER, marker)
    v = Vault(root, dict(DEFAULTS))
    trust.record(v, Library(root).lessons(), "init")
    idx = root / "INDEX.md"
    if idx.exists() and maplib.HEADER.splitlines()[0] not in idx.read_text(encoding="utf-8", errors="replace"):
        print(f"note: kept your own {idx.name}; the generated map is skipped until you move it")
    else:
        maplib.write(v)
    if make_git:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() != root:
            print(f"note: {root} lies inside the git repo {top.stdout.strip()}; the brain gets its own nested history."
                  " A folder outside any repo (e.g. ~/brain) is cleaner.")
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=False)
    if (root / ".git").exists():
        hook = root / ".git" / "hooks" / "pre-commit"
        line = f"{adapters.brain_cmd()} --vault \"{root}\" scan --staged || exit 1  # self-improving-brain secret scan"
        if not hook.exists():
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
            hook.chmod(0o755)
        elif "self-improving-brain secret scan" not in hook.read_text(encoding="utf-8", errors="replace"):
            print(f"note: {hook} exists — add this line to it to block secrets on commit:\n  {line}")
    remember_vault(root)
    if not quiet:
        print(f"✓ vault ready at {root}\nnext:  brain setup   (connects your agents and schedules the nightly learning)")
    return 0


def c_learn(a):
    v = _vault(a)
    files = [Path(a.file)] if a.file else None
    st = learn.scan(v, files)
    if not a.quiet:
        print(f"scanned {st['transcripts']} transcripts · {st['user_messages']} user messages · "
              f"{st['corrections']} corrections · {st['new_drafts']} new drafts (the nightly run judges them)")
    return 0


def c_nightly(a):
    rep = nightly.run(_vault(a), quick=a.quick)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str) if a.json or "skipped" in rep
          else (nightly.summary(rep) + "".join(f"\n! {x}" for x in rep.get("attention") or [])))
    return 0


def c_schedule(a):
    v = _vault(a)
    if a.action == "show":
        target, body = nightly.schedule_plan(v, a.hour)
        print(f"# would write to: {target}\n{body}")
        return 0
    if a.action == "remove":
        print(nightly.schedule_remove(v))
        return 0
    print(nightly.schedule_install(v, a.hour))
    return 0


def c_sync(a):
    print(nightly.sync(_vault(a), a.message or "brain: sync"))
    return 0


def c_config(a):
    v = _vault(a)
    if a.key is None:
        for k in DEFAULTS:
            print(f"{k} = {json.dumps(v.get(k), ensure_ascii=False)}")
        return 0
    if a.value is None:
        print(json.dumps(v.get(a.key), ensure_ascii=False))
        return 0
    if a.key not in DEFAULTS:
        print(f"unknown key {a.key!r}")
        return 1
    val = a.value
    if isinstance(DEFAULTS[a.key], (int, float, bool, list)):
        try:
            val = json.loads(a.value)
        except json.JSONDecodeError:
            print(f"{a.key} expects JSON ({type(DEFAULTS[a.key]).__name__})")
            return 1
    v.set(a.key, val)
    print(f"✓ {a.key} = {json.dumps(val, ensure_ascii=False)}")
    return 0


def c_inbox(a):
    v = _vault(a)
    if a.action in ("done", "drop"):
        ok = inbox.close(v, a.id, "done" if a.action == "done" else "dropped", a.result or "")
        print(("✓ closed" if a.action == "done" else "✓ dropped") if ok else f"no open item {a.id}")
        return 0 if ok else 1
    items = inbox.items(v)
    if not items:
        print("inbox empty")
    for it in items:
        flag = f"  ⚠ {it['conflict']}" if it.get("conflict") else ""
        print(f"[{it['id']}] {it['type']}: {it['claim']}{flag}")
    return 0


def c_distill(a):
    v = _vault(a)
    if a.auto:
        try:
            for line in distill.auto(v):
                print(line)
        except llm.NoLLM as e:
            print(str(e))
            return 1
        return 0
    print(distill.packet(v))
    return 0


def c_approve(a):
    v = _vault(a)
    try:
        l = learn.approve(v, a.id, rule=a.rule or "", mechanism=a.mechanism or "", note=a.note or "",
                          words=_csv(a.words), tools=_csv(a.tools), commands=a.commands or "",
                          paths=a.paths or "", klass=a.klass or "")
    except (KeyError, ValueError) as e:
        print(str(e).strip("'\""))
        return 1
    print(f"✓ lesson {l.id} ({l.mechanism}) written to note {l.note}")
    return 0


def c_reject(a):
    v = _vault(a)
    ok = learn.reject(v, a.id, a.block or None, a.reason or "")
    print("✓ rejected" + (" and topic blocked" if a.block else "") if ok else f"no draft {a.id}")
    return 0 if ok else 1


def c_drafts(a):
    v = _vault(a)
    gs = learn.groups([d for d in learn.drafts(v, None) if d.get("status") in ("open", "held")])
    if not gs:
        print("no drafts waiting")
        return 0
    for g in gs[: a.limit]:
        d = g[0]
        seen = f" · {len({x.get('session') for x in g})} sessions" if len(g) > 1 else ""
        print(f"[{d['id']}] {d.get('status')}{' · ' + d['verdict'] if d.get('verdict') else ''}{seen} · {d.get('source')}")
        print(f"   user said: {short(d.get('quote', ''), 200)}")
        if d.get("rule"):
            print(f"   rule:      {d['rule']}")
    return 0


def c_lesson(a):
    """Add a lesson by hand without a draft."""
    v = _vault(a)
    lib = Library(v.root)
    n = lib.find(a.note) if a.note else None
    if a.note and n is None:
        print(f"no note {a.note!r}")
        return 1
    path = n.path if n else learn.default_note(v)
    l = Lesson(id=learn.unique_lesson_id(lib, a.rule, a.klass or ""), note=n.name if n else "working-style",
               rule=a.rule, mechanism=a.mechanism, tools=_csv(a.tools) or [], commands=a.commands or "",
               paths=a.paths or "", words=_csv(a.words) or [], check=a.check or "", since=today(), klass=a.klass or "")
    learn.write_lesson(v, path, l)
    trust.record(v, [l], "lesson")
    print(f"✓ lesson {l.id} → {path.relative_to(v.root)}")
    return 0


def c_lessons(a):
    v = _vault(a)
    ls = Library(v.root).lessons(retired=a.all)
    if not ls:
        print("no rules yet — they come from your corrections, every night.")
    silent = {l.id: why for l, why in trust.untrusted(v, ls)}
    for l in sorted(ls, key=lambda l: (bool(l.retired), l.mechanism, l.id)):
        trig = ", ".join(x for x in [("words: " + ", ".join(l.words)) if l.words else "",
                                     ("tools: " + ", ".join(l.tools)) if l.tools else "",
                                     ("cmd: " + l.commands) if l.commands else "",
                                     ("path: " + l.paths) if l.paths else "",
                                     ("check: " + l.check) if l.check else ""] if x)
        mark = f"  ⚠ silent: {silent[l.id]} → brain trust {l.id}" if l.id in silent else ""
        mark += f"  (retired {l.retired})" if l.retired else ""
        print(f"{l.mechanism:7} {l.id}  [{l.note}]{mark}\n        {l.rule}" + (f"\n        {trig}" if trig else ""))
    return 0


def c_trust(a):
    v = _vault(a)
    from . import cache
    every = cache.lessons(v)
    silent = trust.untrusted(v, every)
    if a.ids or a.all:
        pick = [l for l, _ in silent if a.all or l.id in a.ids]
        missing = [i for i in a.ids if i not in {l.id for l in every}]
        if missing:
            print("no lesson " + ", ".join(missing))
            return 1
        pick += [l for l in every if l.id in a.ids and l not in pick]
        if not pick:
            print("nothing to trust")
            return 0
        for l in pick:
            print(f"  {l.mechanism:7} {l.id} [{l.note}]: {l.rule}")
        if sys.stdin.isatty() and not a.yes:
            if input(f"Let these {len(pick)} lessons reach your agent? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
                print("nothing changed")
                return 1
        trust.record(v, pick, "trust")
        print(f"✓ {len(pick)} lessons trusted")
        return 0
    if not silent:
        print(f"every lesson is trusted (trust_mode {v.get('trust_mode')})")
        return 0
    print(f"{len(silent)} lessons are silent — read each one before you trust it:")
    for l, why in silent:
        print(f"  {l.id} [{l.note}] · {why}\n     {l.mechanism}: {l.rule}" + (f"\n     check: {l.check}" if l.check else ""))
    print("→ brain trust <id> [<id> …]   or   brain trust --all")
    return 0


def c_promote(a):
    v = _vault(a)
    try:
        l = learn.set_lesson_field(v, a.id, "mechanism", a.to)
    except (KeyError, StopIteration) as e:
        print(str(e).strip("'\""))
        return 1
    print(f"✓ {l.id} is now {l.mechanism}")
    return 0


def c_replay(a):
    v = _vault(a)
    files = [Path(f).expanduser() for f in a.file] if a.file else None
    r = replay.run(v, days=a.days, files=files, draft=a.draft)
    print(json.dumps({k: x for k, x in r.items() if k != "rows" or a.rows}, indent=1, ensure_ascii=False) if a.json
          else replay.render(r))
    return 0


def c_report(a):
    from . import report
    st = report.build(_vault(a))
    if not (a.public or a.badge):
        print(report.text(st))
        return 0
    svg = report.badge(st) if a.badge else report.card(st)
    out = Path(a.out or ("brain-badge.svg" if a.badge else "brain-report.svg")).expanduser()
    out.write_text(svg, encoding="utf-8")
    print(f"✓ {out} — numbers only: no notes, no rules, no quotes, no paths")
    return 0


def c_stats(a):
    v = _vault(a)
    r = metrics.recall_numbers(v)
    print("RECALL (30 days)")
    print(f"  prompts {r['prompts']} · injections {r['injections']} · opened {r['hits']} · "
          f"found without recall {r['finds']} · precision {r['precision'] if r['precision'] is not None else '–'}"
          f" · threshold {v.get('recall_threshold')}")
    print("CORRECTIONS per 100 user messages (lower = smarter agent)")
    curve = metrics.weekly_curve(v)
    if not curve:
        print("  no data yet")
    for row in curve:
        bar = "█" * min(40, int(row["per100"]))
        print(f"  {row['week']}  {row['per100']:5.1f}  {bar}  ({row['corrections']}/{row['messages']})")
    eff = metrics.efficiency(v)
    if eff["share"] is not None:
        print(f"COST  the hooks added {eff['injected']:,} tokens in {eff['calls']:,} calls = {100 * eff['share']:.2f} % of "
              f"{eff['session_tokens']:,} session tokens ({eff['weeks']} weeks)")
    print("LESSONS")
    health = metrics.lesson_health(v)
    if not health:
        print("  none yet")
    for h in health:
        print(f"  {h['id']:40} {h['mechanism']:7} shown {h['shown']:3} · ignored {h['ignored']} · missed {h['missed']}"
              f"{' · broken ' + str(h['violations']) if h['check'] else ''}  → {h['verdict']}")
    checks = events.check_counts(v)
    if checks:
        print("RULE CHECKS (lessons with `check:` — violations found without a user correction)")
        for w, c in list(checks.items())[-8:]:
            rate = 100 * c["violations"] / c["checked"] if c["checked"] else 0
            print(f"  {w}  {c['violations']:3} violations in {c['checked']:4} checked actions ({rate:.1f}%)")
    ds = learn.drafts(v, None)
    print(f"QUEUE  {sum(d.get('status') == 'open' for d in ds)} drafts for the nightly judge · "
          f"{sum(d.get('status') == 'held' for d in ds)} waiting to come back · {len(inbox.items(v))} inbox items · "
          f"llm_cmd {'set' if llm.available(v) else 'not set'}")
    return 0


def c_lint(a):
    v = _vault(a)
    fs = lint(v)
    for f in fs:
        print(f)
    must = sum(f.level == "MUST" for f in fs)
    print(f"{must} MUST · {len(fs) - must} SHOULD")
    return 1 if must else 0


def c_index(a):
    v = _vault(a)
    print("INDEX.md updated" if maplib.write(v) else "INDEX.md unchanged")
    return 0


def c_scan(a):
    if a.staged:
        v = _vault(a)
        res = subprocess.run(["git", "-C", str(v.root), "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
                             capture_output=True)
        if res.returncode != 0:
            print("✗ secret scan could not list staged files: " + res.stderr.decode(errors="replace").strip())
            return 1
        names = [n for n in res.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
        bad = 0
        for name in names:
            show = subprocess.run(["git", "-C", str(v.root), "show", f":{name}"], capture_output=True)
            if show.returncode != 0:
                print(f"✗ {name}: could not read staged content")
                bad += 1
                continue
            for kind, sample in scan(show.stdout.decode("utf-8", errors="replace")):
                print(f"✗ {name}: {kind} ({sample})")
                bad += 1
        if bad:
            print("commit blocked by self-improving-brain secret scan. Remove the value or mark the line with brain:allow-secret.")
        return 1 if bad else 0
    text = sys.stdin.read() if not a.files else "\n".join(Path(f).read_text(encoding="utf-8", errors="replace") for f in a.files)
    found = scan(text)
    for kind, sample in found:
        print(f"✗ {kind} ({sample})")
    return 1 if found else 0


# --- parser ------------------------------------------------------------------------------------------

HIDDEN: list[tuple[str, str]] = []


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain", usage="brain [<command>] [options]", add_help=False)
    p.add_argument("--vault", help="vault folder (default: $BRAIN_VAULT or ~/brain)")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    HIDDEN.clear()

    def add(name: str, text: str, hidden: bool = False, **kw):
        if hidden:  # no `help=`: argparse then leaves it out of every listing
            HIDDEN.append((name, text))
            return sub.add_parser(name, description=text, **kw)
        return sub.add_parser(name, help=text, description=text, **kw)

    s = add("status", "how it is doing (also plain `brain`)", hidden=True); s.set_defaults(f=c_status)
    s = add("review", "what it learned lately, with undo"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--no-input", action="store_true", help="list only, never ask"); s.set_defaults(f=c_review)
    s = add("undo", "take changes back: numbers from `brain review`, or journal ids"); s.add_argument("refs", nargs="+")
    s.set_defaults(f=c_undo)
    s = add("ui", "the whole brain in your browser (local only)")
    s.add_argument("--port", type=int, default=0); s.add_argument("--no-open", action="store_true")
    s.add_argument("--demo", action="store_true", help="example data, not your vault"); s.set_defaults(f=c_ui)
    s = add("setup", "install: vault, agents, history, nightly learning — asks nothing")
    s.add_argument("--vault", dest="setup_vault", metavar="PATH")
    s.add_argument("--github", action="store_true", help="also keep a PRIVATE backup repo on GitHub (needs gh)")
    s.add_argument("--agents", help="comma list to connect, e.g. claude,codex (default: all found)")
    s.add_argument("--no-llm", action="store_true", help="measure only, learn nothing")
    s.add_argument("--no-schedule", action="store_true", help="no nightly run on this machine")
    s.add_argument("--yes", "-y", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--no-github", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(f=c_setup)
    s = add("doctor", "check the installation"); s.set_defaults(f=c_doctor)
    s = add("update", "update self-improving-brain"); s.set_defaults(f=c_update)
    s = add("uninstall", "take the brain out of every agent and stop the nightly run (your notes stay)")
    s.add_argument("agent", nargs="?", default="all", choices=["all", "claude", "codex", "gemini", "agents"])
    s.set_defaults(f=c_uninstall)

    s = add("context", "the session-start context, for agents without hooks", True); s.set_defaults(f=c_context)
    s = add("search", "the best note sections for a question", True); s.add_argument("query")
    s.add_argument("-k", type=int, default=5); s.add_argument("--json", action="store_true"); s.set_defaults(f=c_search)
    s = add("read", "outline of a note, or one section", True); s.add_argument("note"); s.add_argument("section", nargs="?")
    s.add_argument("--all", action="store_true"); s.set_defaults(f=c_read)
    s = add("neighbors", "notes linked to a note", True); s.add_argument("note"); s.set_defaults(f=c_neighbors)
    s = add("remember", "one claim into the inbox; the nightly run files it", True)
    s.add_argument("type", choices=inbox.TYPES); s.add_argument("claim"); s.add_argument("--quote"); s.add_argument("--note")
    s.set_defaults(f=c_remember)
    s = add("check", "would this action trip a guard? (agents without hooks)", True)
    s.add_argument("--command"); s.add_argument("--path"); s.add_argument("--tool"); s.set_defaults(f=c_check)
    s = add("resume", "the last handoff: continue where a session stopped", True)
    s.add_argument("--session"); s.add_argument("--any", action="store_true", help="any repo"); s.add_argument("--list", action="store_true")
    s.set_defaults(f=c_resume)
    s = add("handoff", "write a handoff from a transcript (agents without hooks)", True); s.add_argument("--file")
    s.set_defaults(f=c_handoff)
    s = add("mcp", "the brain as an MCP server (Cursor, Windsurf, Claude Desktop, VS Code, Gemini, Codex, Zed …)", True)
    s.add_argument("action", nargs="?", default="serve", choices=["serve", "install", "uninstall", "config"])
    s.add_argument("client", nargs="?"); s.set_defaults(f=c_mcp)
    s = add("hook", "agent hook entry point", True); s.add_argument("event", choices=list(hooks.HANDLERS))
    s.add_argument("--plugin", action="store_true"); s.set_defaults(f=c_hook)
    s = add("install", "connect one agent", True); s.add_argument("agent", choices=["claude", "codex", "gemini", "agents", "print"])
    s.set_defaults(f=c_install)

    s = add("init", "create an empty vault", True); s.add_argument("path", nargs="?"); s.add_argument("--git", action="store_true")
    s.add_argument("--force", action="store_true"); s.add_argument("--quiet", action="store_true"); s.set_defaults(f=c_init)
    s = add("learn", "mine new transcripts for corrections (no LLM)", True); s.add_argument("--file")
    s.add_argument("--quiet", action="store_true"); s.set_defaults(f=c_learn)
    s = add("nightly", "run the nightly learning once, now", True); s.add_argument("--quick", action="store_true")
    s.add_argument("--json", action="store_true"); s.set_defaults(f=c_nightly)
    s = add("schedule", "the nightly run on this machine (launchd/cron/Task Scheduler)", True)
    s.add_argument("action", choices=["install", "remove", "show"]); s.add_argument("--hour", type=int, default=3)
    s.set_defaults(f=c_schedule)
    s = add("sync", "git commit, pull --rebase, push", True); s.add_argument("-m", "--message"); s.set_defaults(f=c_sync)
    s = add("config", "show or set settings", True); s.add_argument("key", nargs="?"); s.add_argument("value", nargs="?")
    s.set_defaults(f=c_config)
    s = add("inbox", "claims waiting to be filed", True)
    s.add_argument("action", nargs="?", choices=["list", "done", "drop"], default="list"); s.add_argument("id", nargs="?")
    s.add_argument("--result"); s.set_defaults(f=c_inbox)
    s = add("distill", "inbox -> notes (--auto with the LLM; else a work packet for your agent)", True)
    s.add_argument("--auto", action="store_true"); s.set_defaults(f=c_distill)
    s = add("drafts", "corrections waiting for the nightly judge or for a second time", True)
    s.add_argument("--limit", type=int, default=20); s.set_defaults(f=c_drafts)
    s = add("approve", "turn a draft into a rule by hand", True)
    s.add_argument("id"); s.add_argument("--rule"); s.add_argument("--mechanism", choices=["reflex", "always", "guard", "skill"])
    s.add_argument("--note"); s.add_argument("--words"); s.add_argument("--tools"); s.add_argument("--commands")
    s.add_argument("--paths"); s.add_argument("--class", dest="klass"); s.set_defaults(f=c_approve)
    s = add("reject", "discard a draft", True); s.add_argument("id"); s.add_argument("--block", nargs="*")
    s.add_argument("--reason"); s.set_defaults(f=c_reject)
    s = add("lesson", "add a rule by hand", True)
    s.add_argument("rule"); s.add_argument("--note"); s.add_argument("--mechanism", default="reflex", choices=["reflex", "always", "guard", "skill"])
    s.add_argument("--words"); s.add_argument("--tools"); s.add_argument("--commands"); s.add_argument("--paths")
    s.add_argument("--check"); s.add_argument("--class", dest="klass"); s.set_defaults(f=c_lesson)
    s = add("lessons", "every rule (--all: retired ones too)", True); s.add_argument("--all", action="store_true")
    s.set_defaults(f=c_lessons)
    s = add("trust", "let rules an agent wrote reach your agent", True)
    s.add_argument("ids", nargs="*"); s.add_argument("--all", action="store_true"); s.add_argument("--yes", "-y", action="store_true")
    s.set_defaults(f=c_trust)
    s = add("promote", "change how a rule acts (reflex, always, guard)", True)
    s.add_argument("id"); s.add_argument("--to", required=True, choices=["reflex", "always", "guard", "skill"]); s.set_defaults(f=c_promote)
    s = add("replay", "past corrections against today's rules: caught or missed?", True)
    s.add_argument("--days", type=int, default=30); s.add_argument("--file", action="append")
    s.add_argument("--draft", action="store_true"); s.add_argument("--json", action="store_true")
    s.add_argument("--rows", action="store_true"); s.set_defaults(f=c_replay)
    s = add("report", "report card (--public: a shareable SVG, numbers only)", True)
    s.add_argument("--public", action="store_true"); s.add_argument("--badge", action="store_true")
    s.add_argument("-o", "--out"); s.set_defaults(f=c_report)
    s = add("stats", "every number behind `brain`", True); s.set_defaults(f=c_stats)
    s = add("lint", "check the vault", True); s.set_defaults(f=c_lint)
    s = add("index", "regenerate INDEX.md", True); s.set_defaults(f=c_index)
    s = add("scan", "secret scan (stdin, files, or --staged)", True); s.add_argument("files", nargs="*")
    s.add_argument("--staged", action="store_true"); s.set_defaults(f=c_scan)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pre = argv[:2] if argv[:1] == ["--vault"] else []
    rest = argv[len(pre):]
    if not rest:
        argv = pre + ["status"]
    elif rest[0] in ("-h", "--help") or rest == ["help"]:
        print(HELP)
        return 0
    elif rest[0] == "help":
        build_parser()
        print("Everything else, mostly for agents and scripts (each has --help):\n")
        for name, text in HIDDEN:
            print(f"  {name:<11} {text}")
        return 0
    a = build_parser().parse_args(argv)
    if not getattr(a, "f", None):
        print(HELP)
        return 2
    try:
        return a.f(a) or 0
    except NotAVault as e:
        print(str(e))
        return 2
    except BrokenPipeError:
        return 0
