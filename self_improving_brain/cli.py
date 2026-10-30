"""`brain` — the command line every agent (and you) talk to."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import adapters, distill, events, handoff, hooks, inbox, learn, llm, maplib, metrics, nightly, packs, replay, trust
from .config import DEFAULTS, MARKER, NotAVault, Vault, is_vault, open_vault, remember_vault
from .lint import lint
from .notes import Lesson, Library
from .search import Index, learned_model, snippet
from .secrets import scan
from .util import save_json, short, today



def _vault(a) -> Vault:
    return open_vault(a.vault)


def _ses() -> str:
    return os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("BRAIN_SESSION") or "cli"


def _log_read(v: Vault, note: str) -> None:
    # Inside Claude Code the post-tool hook logs this read with the real session id.
    # Log here only when we know the session; a "cli" session would skew recall precision.
    if _ses() != "cli":
        events.log(v, "read", session=_ses(), note=note, via="cli")


# --- commands ----------------------------------------------------------------------------

def c_init(a):
    root = Path(a.path or "~/brain").expanduser().resolve()
    if is_vault(root):
        print(f"{root} is already an self-improving-brain vault.")
        return 0
    if (root / ".brain").exists():
        print(f"refusing: {root}/.brain exists but is not an self-improving-brain vault. Pick another folder.")
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
        print(f"✓ vault ready at {root}")
        print("next:  brain setup               (connects your agents, backup, nightly run — recommended)")
        print("   or  brain install claude      (codex | gemini | agents | print) and brain schedule install")
    return 0


def c_setup(a):
    from . import setup
    a.vault = a.setup_vault or a.vault
    return setup.run(a)


def c_refine(a):
    v = _vault(a)
    if not llm.available(v):
        print("no llm_cmd configured — `brain config llm_cmd \"<cli>\"` or let your agent judge in `brain review`")
        return 1
    print(f"judged {learn.refine(v, batches=10)} candidates")
    return 0


def c_update(a):
    root = Path(__file__).resolve().parent.parent
    if (root / ".git").exists():
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "-q"], capture_output=True, text=True)
        print("✓ updated" if r.returncode == 0 else "update failed: " + r.stderr.strip()[:300])
        return r.returncode
    print("installed as a package — update with:  pipx upgrade self-improving-brain")
    return 0


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
        print("Lessons here: " + ", ".join(l.id for l in n.lessons))
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
    print(f"✓ {rec['id']} in inbox ({rec['type']}). It gets distilled into the wiki later (`brain distill`).")
    return 0


def c_inbox(a):
    v = _vault(a)
    if a.action == "done":
        ok = inbox.close(v, a.id, "done", a.result or "")
        print("✓ closed" if ok else f"no open item {a.id}")
        return 0 if ok else 1
    if a.action == "drop":
        ok = inbox.close(v, a.id, "dropped", a.result or "")
        print("✓ dropped" if ok else f"no open item {a.id}")
        return 0 if ok else 1
    items = inbox.items(v)
    if not items:
        print("inbox empty")
    for it in items:
        flag = f"  ⚠ CONFLICT: {it['conflict']}" if it.get("conflict") else ""
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


def c_learn(a):
    v = _vault(a)
    files = [Path(a.file)] if a.file else None
    st = learn.scan(v, files)
    if not a.quiet:
        print(f"scanned {st['transcripts']} transcripts · {st['user_messages']} user messages · "
              f"{st['corrections']} corrections · {st['new_drafts']} new drafts"
              + (f" · {st['refined']} refined by llm" if st.get("refined") else ""))
        if st["new_drafts"]:
            print("→ brain review")
    return 0


def c_review(a):
    v = _vault(a)
    gs = learn.groups(learn.drafts(v))
    if not gs:
        print("no open lesson candidates — keep working, `brain learn` picks up new corrections")
        return 0
    print(f"{len(gs)} lesson candidates (similar corrections grouped, strongest first).\n"
          "For each real, reusable mistake write ONE imperative rule and approve it; reject noise.\n"
          "Show the user what you propose and approve only with their OK.\n")
    for g in gs[: a.limit]:
        d = g[0]
        seen = f" · seen {len(g)}×" if len(g) > 1 else ""
        print(f"[{d['id']}] {d['kind']} · confidence {float(d.get('score', 0.5)):.2f}{seen} · {d['source']} · {str(d.get('ts', ''))[:10]}")
        if d.get("context"):
            print(f"   agent did: {short(d['context'], 200)}")
        print(f"   user said: {short(d['quote'], 300)}")
        for other in g[1:3]:
            print(f"   also:      {short(other['quote'], 160)}")
        if d.get("rule"):
            sug = d.get("suggest") or {}
            print(f"   suggested rule: {d['rule']}  [{sug.get('mechanism', 'reflex')}; words: {', '.join(sug.get('words') or [])}]")
    if len(gs) > a.limit:
        print(f"\n… {len(gs) - a.limit} more (brain review --limit {len(gs)})")
    print('\n→ brain approve <id> --rule "<rule>" [--mechanism reflex|always|guard] [--note <note>] '
          '[--words "a, b"] [--commands REGEX] [--tools name]')
    print("→ brain reject <id> [--block word word]   (block = never draft this topic again)")
    return 0


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s is not None else None


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
    from . import cache
    ls = cache.lessons(v)
    if not ls:
        print("no lessons yet — they appear as you correct your agent (`brain learn`, `brain review`).")
    silent = {l.id: why for l, why in trust.untrusted(v, ls)}
    for l in sorted(ls, key=lambda l: (l.mechanism, l.id)):
        trig = ", ".join(x for x in [("words: " + ", ".join(l.words)) if l.words else "",
                                     ("tools: " + ", ".join(l.tools)) if l.tools else "",
                                     ("cmd: " + l.commands) if l.commands else "",
                                     ("path: " + l.paths) if l.paths else "",
                                     ("check: " + l.check) if l.check else ""] if x)
        mark = f"  ⚠ silent: {silent[l.id]} → brain trust {l.id}" if l.id in silent else ""
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


def c_replay(a):
    v = _vault(a)
    files = [Path(f).expanduser() for f in a.file] if a.file else None
    r = replay.run(v, days=a.days, files=files, draft=a.draft)
    print(json.dumps({k: x for k, x in r.items() if k != "rows" or a.rows}, indent=1, ensure_ascii=False) if a.json
          else replay.render(r))
    return 0


def _show_pack(p: dict) -> None:
    print(f"pack {p['name']} v{p['version']} · {p['origin']}" + (f"\n  {p['description']}" if p["description"] else ""))
    for l in p["lessons"]:
        trig = l.commands or l.check or ", ".join(l.words) or l.paths
        print(f"  {l.mechanism:7} {l.id}: {l.rule}" + (f"\n          ↳ {trig}" if trig else ""))
    for i in p["skipped"]:
        print(f"  (skipped {i}: you already have a lesson with this id)")
    for w in p["warnings"]:
        print(f"  ⚠ {w}")
    for e in p["problems"]:
        print(f"  ✗ {e}")


def c_pack(a):
    v = _vault(a)
    try:
        if a.action == "list":
            for p in packs.listing(v):
                mark = "✓" if p["installed"] else " "
                print(f"{mark} {p['name']:12} {p['lessons']:2} lessons  {p['description'][:90]}"
                      + ("  (update: `brain pack add " + p["name"] + "`)" if p["outdated"] else ""))
            print("→ brain pack show <name> · brain pack add <name|file|https-url>")
            return 0
        if not a.name:
            print(f"brain pack {a.action} needs a name")
            return 1
        if a.action == "show":
            _show_pack(packs.plan(v, a.name))
            return 0
        if a.action == "add":
            p = packs.plan(v, a.name)
            if p["origin"] != "builtin" and not a.yes:
                _show_pack(p)
                if not sys.stdin.isatty():
                    print("a pack from outside puts rules into your agent — read them, then run again with --yes")
                    return 2
                if input("trust these lessons and add them? [y/N] ").strip().lower() not in ("y", "yes"):
                    return 1
            p = packs.add(v, a.name, force=a.force)
            print(f"✓ pack {p['name']}: {len(p['lessons'])} lessons → {p['path'].relative_to(v.root)}"
                  + (f" ({len(p['skipped'])} skipped, ids already in your vault)" if p["skipped"] else ""))
            maplib.write(v)
            return 0
        if a.action == "remove":
            print(f"✓ removed {packs.remove(v, a.name, force=a.force).relative_to(v.root)}")
            maplib.write(v)
            return 0
        text = packs.export(v, a.name, a.lesson or [], note=a.note or "", description=a.description or "")
        if a.out:
            Path(a.out).expanduser().write_text(text, encoding="utf-8")
            print(f"✓ wrote {a.out} — share it; others add it with `brain pack add <file or https url>`")
        else:
            print(text, end="")
        return 0
    except (ValueError, OSError) as e:
        print(f"✗ {e}")
        return 1


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


def c_ui(a):
    from . import ui
    return ui.serve(None if a.demo else _vault(a), port=a.port, open_browser=not a.no_open)


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


def c_skills(a):
    from . import skills
    v = _vault(a)
    try:
        if a.action == "mine":
            ds = skills.mine(v, days=a.days)
            print(f"{len(ds)} procedures came back in ≥{skills.MIN_SESSIONS} sessions")
        if a.action in ("mine", "list"):
            for d in skills.drafts(v)[:a.limit]:
                print(f"{d['id']}  {d['sessions']:3}×  {d['name']:28} {' → '.join(d['steps'])}")
            for s in skills.installed(v):
                print(f"✓ {s['name']:28} {s['description'][:80]}")
            if skills.drafts(v):
                print('→ brain skills approve <id> --name <name> --description "<when to use it>"  ·  brain skills reject <id>')
            elif not skills.installed(v):
                print("no recurring procedures yet — `brain skills mine` looks again")
            return 0
        if a.action == "show":
            d = next((x for x in skills.drafts(v, None) if x["id"] == a.id), None)
            if d is None:
                print(f"no skill draft {a.id}")
                return 1
            print(f"{d['id']} · {d['sessions']} sessions · status {d['status']}")
            for i, (st, ex) in enumerate(zip(d["steps"], d["examples"]), 1):
                print(f"  {i}. {ex}")
            for p in d.get("prompts", [])[:3]:
                print(f"  started by: “{p[:120]}”")
            return 0
        if a.action == "approve":
            r = skills.approve(v, a.id or "", name=a.name or "", description=a.description or "",
                               words=_csv(a.words), claude=not a.no_claude)
            print(f"✓ {r['note'].relative_to(v.root)} · lesson {r['lesson']}" + (f" · Claude Code skill {r['claude']}" if r["claude"] else ""))
            maplib.write(v)
            return 0
        if a.action == "reject":
            print("✓ rejected" if skills.reject(v, a.id or "") else f"no skill draft {a.id}")
            return 0
        for p in skills.sync(v):
            print(f"✓ {p}")
        return 0
    except (KeyError, ValueError) as e:
        print(f"✗ {str(e).strip(chr(39))}")
        return 1


def c_atlas(a):
    from . import atlas
    v = _vault(a)
    dest, at = atlas.write(v, Path(a.path or os.getcwd()).expanduser())
    print(f"✓ atlas of {at['repo']} ({at['files']} files, commit {at['sha'][:10] or '–'}) → {dest.relative_to(v.root)}")
    if a.show:
        print(atlas.render(at))
    maplib.write(v)
    return 0


def c_ingest(a):
    from . import ingest
    v = _vault(a)
    if a.refresh:
        rows = ingest.refresh(v)
        for r in rows:
            extra = f" → {len(r.get('flagged', []))} notes to re-check" if r.get("status") == "changed" else ""
            print(f"{r['status']:10} {r.get('note', '')}  {r['source'][:80]}{extra}")
        if not rows:
            print("no sources yet — `brain ingest <file|https-url|folder>`")
        return 0
    if not a.source:
        print("brain ingest <file|https-url|folder>  ·  brain ingest --refresh")
        return 1
    srcs = ingest.sources(v, a.source) if Path(a.source).expanduser().is_dir() else [a.source]
    rc = 0
    for s in srcs:
        try:
            r = ingest.add(v, s)
            print(f"✓ {r['status']:9} {r['note']}" + (f" → {len(r['flagged'])} notes to re-check" if r.get("flagged") else ""))
        except (ValueError, OSError) as e:
            print(f"✗ {e}")
            rc = 1
    maplib.write(v)
    print("→ `brain distill` (or /brain-distill) works the new sources into your notes")
    return rc


def _review_team(name: str, rows, yes: bool) -> bool:
    if not rows:
        print(f"team {name}: nothing new")
        return False
    print(f"team {name}: {len(rows)} lessons to accept")
    for l, why in rows:
        print(f"  {why:7} {l.mechanism:7} {l.id}: {l.rule}")
    if yes:
        return True
    if not sys.stdin.isatty():
        print("these rules would reach your agent — read them, then run again with --yes")
        return False
    return input("accept them? [y/N] ").strip().lower() in ("y", "yes")


def c_team(a):
    from . import team
    v = _vault(a)
    try:
        if a.action == "list":
            led = team._ledger(v)
            for n in team.names(v):
                waiting = len(team.pending(v, n))
                print(f"{n:16} {len(team.lessons(v, n)):3} lessons · {led.get(n, {}).get('url', '')}"
                      + (f" · {waiting} waiting for your OK (`brain team sync {n}`)" if waiting else ""))
            if not team.names(v):
                print("no team brain — `brain team add <name> <git-url>`")
            return 0
        if a.action == "add":
            if not a.name or not a.arg:
                print("brain team add <name> <git-url>")
                return 1
            rows = team.clone(v, a.name, a.arg)
            if _review_team(a.name, rows, a.yes):
                team.accept(v, a.name, [l for l, _ in rows])
                print(f"✓ {len(rows)} team lessons active")
            return 0
        if a.action == "sync":
            for n in [a.name] if a.name else team.names(v):
                rows = team.pull(v, n)
                if _review_team(n, rows, a.yes):
                    team.accept(v, n, [l for l, _ in rows])
                    print(f"✓ team {n}: {len(rows)} lessons accepted")
            return 0
        if a.action == "remove":
            print(f"✓ removed {team.remove(v, a.name or '')}")
            return 0
        lesson = next((l for l in Library(v.root).lessons() if l.id == a.name), None)
        if lesson is None:
            print(f"no lesson {a.name} in your vault")
            return 1
        target = a.arg or (team.names(v)[0] if len(team.names(v)) == 1 else "")
        if not target:
            print("which team? brain team propose <lesson-id> <team>")
            return 1
        r = team.propose(v, lesson, target)
        print(f"✓ {lesson.id} on branch {r['branch']}" + (" (pushed)" if r["pushed"] else f" — push failed: {r.get('push_error')}"))
        if r.get("pr") and r["pushed"]:
            print(f"→ open the pull request: {r['pr']}")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(f"✗ {e}")
        return 1


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
        print("  no data yet — run `brain learn`")
    for row in curve:
        bar = "█" * min(40, int(row["per100"]))
        print(f"  {row['week']}  {row['per100']:5.1f}  {bar}  ({row['corrections']}/{row['messages']})")
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
    d = len(learn.drafts(v))
    i = len(inbox.items(v))
    print(f"QUEUE  {d} lesson drafts · {i} inbox items · llm_cmd {'set' if llm.available(v) else 'not set (agent-driven mode)'}")
    return 0


def c_nightly(a):
    v = _vault(a)
    rep = nightly.run(v, quick=a.quick)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 0


def c_schedule(a):
    v = _vault(a)
    if a.action == "show":
        target, body = nightly.schedule_plan(v, a.hour)
        print(f"# would write to: {target}\n{body}")
        return 0
    if a.action == "remove":
        print(nightly.schedule_remove())
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


def c_hook(a):
    return hooks.run(a.event, a.vault, plugin=a.plugin)


def c_install(a):
    v = _vault(a)
    for line in adapters.install(v, a.agent):
        print(line)
    return 0


def c_uninstall(a):
    for line in adapters.uninstall(a.agent):
        print(line)
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


def c_doctor(a):
    ok = True
    try:
        v = _vault(a)
        print(f"✓ vault {v.root}")
    except NotAVault as e:
        print(f"✗ {e}")
        return 1
    lib = Library(v.root)
    print(f"✓ {len(lib.notes)} notes · {len(lib.lessons())} lessons · {sum(len(n.links) for n in lib.notes.values())} links")
    st = Path.home() / ".claude" / "settings.json"
    claude = st.exists() and adapters.HOOK_TAG in st.read_text(encoding="utf-8")
    print(("✓" if claude else "·") + " Claude Code hooks " + ("installed" if claude else "not installed (brain install claude)"))
    for name, p in [("codex", Path.home() / ".codex" / "AGENTS.md"), ("gemini", Path.home() / ".gemini" / "GEMINI.md")]:
        on = p.exists() and adapters.BEGIN in p.read_text(encoding="utf-8")
        print(("✓ " if on else "· ") + f"{name} " + ("connected" if on else f"not connected (brain install {name})"))
    files = learn.transcript_files(v)
    print(f"{'✓' if files else '·'} {len(files)} transcripts visible for learning")
    print(("✓ llm_cmd: " + v.get("llm_cmd")) if llm.available(v) else "· llm_cmd not set — drafts are refined by your agent in `brain review`")
    if llm.available(v):
        exe = v.get("llm_cmd").split()[0]
        if not shutil.which(exe):
            print(f"✗ {exe} not on PATH")
            ok = False
    url, problem = nightly.backup_state(v.root)
    if not url:
        print("! no backup remote — if this disk dies, the brain dies too (brain setup → step 6)")
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
    rep = v.path("state", "nightly.json")
    print(("✓ last nightly run " + json.loads(rep.read_text())["t"]) if rep.exists() else "· nightly never ran (brain schedule install)")
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


# --- parser ------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain", description="self-improving-brain: a self-improving, local memory for any coding agent.")
    p.add_argument("--vault", help="vault folder (default: $BRAIN_VAULT or ~/brain)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="one command: vault, agents, history, backup, nightly (start here)")
    s.add_argument("--vault", dest="setup_vault", metavar="PATH"); s.add_argument("--yes", "-y", action="store_true", help="accept all defaults")
    s.add_argument("--agents", help="comma list to connect, e.g. claude,codex (default: all detected)")
    s.add_argument("--no-github", action="store_true"); s.add_argument("--no-llm", action="store_true"); s.add_argument("--no-schedule", action="store_true")
    s.add_argument("--no-packs", action="store_true")
    s.set_defaults(f=c_setup)
    s = sub.add_parser("init", help="create a vault"); s.add_argument("path", nargs="?"); s.add_argument("--git", action="store_true"); s.add_argument("--force", action="store_true"); s.add_argument("--quiet", action="store_true"); s.set_defaults(f=c_init)
    s = sub.add_parser("refine", help="let llm_cmd judge open lesson candidates now"); s.set_defaults(f=c_refine)
    s = sub.add_parser("update", help="update self-improving-brain itself"); s.set_defaults(f=c_update)
    s = sub.add_parser("context", help="print the session-start context (for agents without hooks)"); s.set_defaults(f=c_context)
    s = sub.add_parser("search", help="find the best sections for a question"); s.add_argument("query"); s.add_argument("-k", type=int, default=5); s.add_argument("--json", action="store_true"); s.set_defaults(f=c_search)
    s = sub.add_parser("read", help="outline of a note, or one section"); s.add_argument("note"); s.add_argument("section", nargs="?"); s.add_argument("--all", action="store_true"); s.set_defaults(f=c_read)
    s = sub.add_parser("neighbors", help="linked notes"); s.add_argument("note"); s.set_defaults(f=c_neighbors)
    s = sub.add_parser("remember", help="put one claim into the inbox"); s.add_argument("type", choices=inbox.TYPES); s.add_argument("claim"); s.add_argument("--quote"); s.add_argument("--note"); s.set_defaults(f=c_remember)
    s = sub.add_parser("inbox", help="list / close inbox items"); s.add_argument("action", nargs="?", choices=["list", "done", "drop"], default="list"); s.add_argument("id", nargs="?"); s.add_argument("--result"); s.set_defaults(f=c_inbox)
    s = sub.add_parser("distill", help="inbox -> wiki (packet for your agent, or --auto with llm_cmd)"); s.add_argument("--auto", action="store_true"); s.set_defaults(f=c_distill)
    s = sub.add_parser("learn", help="mine transcripts for corrections -> lesson drafts"); s.add_argument("--file"); s.add_argument("--quiet", action="store_true"); s.set_defaults(f=c_learn)
    s = sub.add_parser("review", help="show lesson drafts"); s.add_argument("--limit", type=int, default=15); s.set_defaults(f=c_review)
    s = sub.add_parser("approve", help="turn a draft into a lesson")
    s.add_argument("id"); s.add_argument("--rule"); s.add_argument("--mechanism", choices=["reflex", "always", "guard", "skill"]); s.add_argument("--note")
    s.add_argument("--words"); s.add_argument("--tools"); s.add_argument("--commands"); s.add_argument("--paths"); s.add_argument("--class", dest="klass"); s.set_defaults(f=c_approve)
    s = sub.add_parser("reject", help="discard a draft"); s.add_argument("id"); s.add_argument("--block", nargs="*"); s.add_argument("--reason"); s.set_defaults(f=c_reject)
    s = sub.add_parser("lesson", help="add a lesson by hand")
    s.add_argument("rule"); s.add_argument("--note"); s.add_argument("--mechanism", default="reflex", choices=["reflex", "always", "guard", "skill"])
    s.add_argument("--words"); s.add_argument("--tools"); s.add_argument("--commands"); s.add_argument("--paths"); s.add_argument("--check"); s.add_argument("--class", dest="klass"); s.set_defaults(f=c_lesson)
    s = sub.add_parser("lessons", help="list all lessons"); s.set_defaults(f=c_lessons)
    s = sub.add_parser("trust", help="review lessons that stay silent (written by an agent, or strict mode)")
    s.add_argument("ids", nargs="*"); s.add_argument("--all", action="store_true"); s.add_argument("--yes", "-y", action="store_true"); s.set_defaults(f=c_trust)
    s = sub.add_parser("promote", help="change how a lesson acts, e.g. reflex -> guard after repeated violations")
    s.add_argument("id"); s.add_argument("--to", required=True, choices=["reflex", "always", "guard", "skill"]); s.set_defaults(f=c_promote)
    s = sub.add_parser("resume", help="show the last handoff (continue where a session stopped)")
    s.add_argument("--session"); s.add_argument("--any", action="store_true", help="any repo"); s.add_argument("--list", action="store_true"); s.set_defaults(f=c_resume)
    s = sub.add_parser("handoff", help="write a handoff from a session transcript (agents without hooks)")
    s.add_argument("--file"); s.set_defaults(f=c_handoff)
    s = sub.add_parser("check", help="would this action trip a guard lesson? (agents without hooks)")
    s.add_argument("--command"); s.add_argument("--path"); s.add_argument("--tool"); s.set_defaults(f=c_check)
    s = sub.add_parser("replay", help="replay past sessions: which corrections would today's lessons have caught?")
    s.add_argument("--days", type=int, default=30); s.add_argument("--file", action="append", help="one transcript (repeatable)")
    s.add_argument("--draft", action="store_true", help="turn uncovered corrections into lesson drafts")
    s.add_argument("--json", action="store_true"); s.add_argument("--rows", action="store_true", help="with --json: every correction")
    s.set_defaults(f=c_replay)
    s = sub.add_parser("pack", help="lesson packs: ready-made rules (safety, secrets, testing, git, python, typescript)")
    s.add_argument("action", choices=["list", "show", "add", "remove", "export"]); s.add_argument("name", nargs="?")
    s.add_argument("--yes", action="store_true"); s.add_argument("--force", action="store_true")
    s.add_argument("--lesson", action="append", help="export: lesson id (repeatable)"); s.add_argument("--note", help="export: every lesson of a note")
    s.add_argument("--description"); s.add_argument("-o", "--out"); s.set_defaults(f=c_pack)
    s = sub.add_parser("mcp", help="the brain as an MCP server (Cursor, Windsurf, Claude Desktop, VS Code, Gemini, Codex, Zed …)")
    s.add_argument("action", nargs="?", default="serve", choices=["serve", "install", "uninstall", "config"])
    s.add_argument("client", nargs="?", help="claude-desktop, cursor, windsurf, gemini, vscode, codex (install: default all detected); config also: zed")
    s.set_defaults(f=c_mcp)
    s = sub.add_parser("ui", help="the brain in your browser: progress, lessons, review, replay, graph (local only)")
    s.add_argument("--port", type=int, default=0); s.add_argument("--no-open", action="store_true")
    s.add_argument("--demo", action="store_true", help="example data, not your vault"); s.set_defaults(f=c_ui)
    s = sub.add_parser("report", help="report card: is your agent getting better? (--public: a shareable SVG, numbers only)")
    s.add_argument("--public", action="store_true"); s.add_argument("--badge", action="store_true", help="a small README badge")
    s.add_argument("-o", "--out"); s.set_defaults(f=c_report)
    s = sub.add_parser("skills", help="procedures your agent repeated, turned into skills (with your OK)")
    s.add_argument("action", nargs="?", default="list", choices=["list", "mine", "show", "approve", "reject", "sync"])
    s.add_argument("id", nargs="?"); s.add_argument("--name"); s.add_argument("--description"); s.add_argument("--words")
    s.add_argument("--days", type=int, default=60); s.add_argument("--limit", type=int, default=15)
    s.add_argument("--no-claude", action="store_true", help="approve: vault note and lesson only"); s.set_defaults(f=c_skills)
    s = sub.add_parser("atlas", help="map a codebase (layout, entry points, test command, key modules) into its repo note")
    s.add_argument("path", nargs="?"); s.add_argument("--show", action="store_true"); s.set_defaults(f=c_atlas)
    s = sub.add_parser("ingest", help="store a file, https page or folder as source notes; --refresh flags notes whose source changed")
    s.add_argument("source", nargs="?"); s.add_argument("--refresh", action="store_true"); s.set_defaults(f=c_ingest)
    s = sub.add_parser("team", help="a team brain: shared lessons from a git repo, accepted by you")
    s.add_argument("action", nargs="?", default="list", choices=["list", "add", "sync", "propose", "remove"])
    s.add_argument("name", nargs="?", help="team name (propose: lesson id)"); s.add_argument("arg", nargs="?", help="add: git url · propose: team")
    s.add_argument("--yes", action="store_true"); s.set_defaults(f=c_team)
    s = sub.add_parser("lint", help="check the vault"); s.set_defaults(f=c_lint)
    s = sub.add_parser("index", help="regenerate INDEX.md"); s.set_defaults(f=c_index)
    s = sub.add_parser("stats", help="is the brain getting smarter?"); s.set_defaults(f=c_stats)
    s = sub.add_parser("nightly", help="run the autonomous loop once"); s.add_argument("--quick", action="store_true"); s.set_defaults(f=c_nightly)
    s = sub.add_parser("schedule", help="run nightly on this laptop (launchd/cron)"); s.add_argument("action", choices=["install", "remove", "show"]); s.add_argument("--hour", type=int, default=3); s.set_defaults(f=c_schedule)
    s = sub.add_parser("sync", help="git commit, pull --rebase, push"); s.add_argument("-m", "--message"); s.set_defaults(f=c_sync)
    s = sub.add_parser("config", help="show or set settings"); s.add_argument("key", nargs="?"); s.add_argument("value", nargs="?"); s.set_defaults(f=c_config)
    s = sub.add_parser("hook", help="(internal) agent hook entry point"); s.add_argument("event", choices=list(hooks.HANDLERS)); s.add_argument("--plugin", action="store_true"); s.set_defaults(f=c_hook)
    s = sub.add_parser("install", help="connect an agent"); s.add_argument("agent", choices=["claude", "codex", "gemini", "agents", "print"]); s.set_defaults(f=c_install)
    s = sub.add_parser("uninstall", help="disconnect an agent"); s.add_argument("agent", choices=["claude", "codex", "gemini", "agents"]); s.set_defaults(f=c_uninstall)
    s = sub.add_parser("scan", help="secret scan (stdin, files, or --staged)"); s.add_argument("files", nargs="*"); s.add_argument("--staged", action="store_true"); s.set_defaults(f=c_scan)
    s = sub.add_parser("doctor", help="check the setup"); s.set_defaults(f=c_doctor)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.f(a) or 0
    except NotAVault as e:
        print(str(e))
        return 2
    except BrokenPipeError:
        return 0
