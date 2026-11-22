"""The autonomous loop. Runs on your laptop via launchd (macOS), cron (Linux) or the Task Scheduler — no server.

  pull       what your other machines learned (if the vault syncs through git)
  learn      mine new transcripts for your corrections (no LLM)
  probe      one tiny LLM call: unreachable (asleep, offline) = a broken night, tried again in a few hours
  outcomes   what happened after the agent's work (red CI, a revert, a change request, a failing deploy, a tool
             failing in a row): read through `gh` (no LLM), then one LLM call judges whether it was the agent's
             mistake; a lesson becomes a draft like a correction, knowledge goes to the inbox  (budgeted)
  judge      the LLM sorts them: lasting rule, one-off steering, fact to remember, or noise   (budgeted)
  merge      the LLM joins drafts that ask for the same thing in other words                 (budgeted)
  adopt      policy, no LLM: what you said twice or as "always/never" becomes a rule; a rule that comes
             back gets sharpened, then escalated
  github     the pull requests and issues your notes mention: a line that calls a merged one open
             becomes a claim, so it is corrected the same night (no LLM, one batched `gh` call)
  extract    the LLM reads what is new in your sessions for decisions, facts, pitfalls       (budgeted)
  distill    files them into the notes; a newer statement replaces an outdated one           (budgeted)
  answers    an always rule the answer check finds broken again and again is worded sharper  (budgeted)
  curate     quiet rules retire, the rule set stays within its caps
  care       upkeep: descriptions, orphans, triggers, notes that carry the same topic are merged     (budgeted)
  themes     two rules that demand the same thing become one, the older one carries it             (budgeted)
  replay     past corrections against today's rules: would they have been caught?
  cache      rebuild the learned recall model; autotune the recall threshold from its precision
  index      regenerate INDEX.md, lint the vault
  sync       git commit + pull --rebase + push (if the vault is a git repo with a remote)

Every change it makes is in the journal: `brain review` shows it, `brain undo` takes it back.
The LLM steps stop when the day's budget (`llm_budget_usd`, `llm_max_calls`) is used up.
"""
from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Vault
from .util import day_label, days_since, load_json, now_iso, save_json, short

LABEL = "dev.self-improving-brain.nightly"


def git_in(root: Path, *args: str) -> subprocess.CompletedProcess:
    # never wait for a password prompt or a dead connection: this runs at night, without anyone at the keyboard,
    # and holds the lock while it waits
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=300,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 124, "", "git timed out after 300 s")


def git(v: Vault, *args: str) -> subprocess.CompletedProcess:
    return git_in(v.root, *args)


# the host must be github.com itself, not a path that mentions it (https://evil.example/github.com/me/x)
GITHUB = re.compile(r"^(?:(?:https?|ssh|git)://(?:[^@/\s]+@)?|[^@/\s:]+@)?github\.com(?::\d+)?[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def remote_visibility(url: str) -> str:
    """PRIVATE, PUBLIC or INTERNAL for a GitHub remote, '' if unknown. A brain must never land in public."""
    m = GITHUB.search(url.strip())
    if not m:
        return ""
    full = f"{m.group(1)}/{m.group(2)}"
    try:
        if shutil.which("gh"):
            r = subprocess.run(["gh", "repo", "view", full, "--json", "visibility", "-q", ".visibility"],
                               capture_output=True, text=True, timeout=20, env=dict(os.environ, GH_HOST="github.com"))
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().upper()
        # without gh: whatever anyone can read anonymously is public
        env = {k: val for k, val in os.environ.items() if k not in ("GIT_ASKPASS", "SSH_ASKPASS")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        r = subprocess.run(["git", "-c", "credential.helper=", "-c", "core.askPass=", "ls-remote",
                            f"https://github.com/{full}.git", "HEAD"], capture_output=True, text=True, timeout=20, env=env)
        return "PUBLIC" if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def backup_state(root: Path) -> tuple[str, str]:
    """(remote url, problem). No problem means a remote exists and every commit is on it."""
    names = git_in(root, "remote").stdout.split()
    if not names:
        return "", "no backup remote"
    url = git_in(root, "remote", "get-url", "origin" if "origin" in names else names[0]).stdout.strip()
    if git_in(root, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
        return url, "nothing pushed yet"
    ahead = git_in(root, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
    return url, (f"{ahead} commits not pushed yet" if ahead not in ("", "0") else "")


def identity(root: Path) -> list[str]:
    """A fresh machine, container or CI box has no git name: the brain commits as `brain` there."""
    return [] if git_in(root, "config", "user.email").stdout.strip() else \
        ["-c", "user.name=brain", "-c", "user.email=brain@localhost"]


def _commit(v: Vault, message: str) -> str:
    """Commit what the brain may commit. '' when that worked, else the problem."""
    if v.get("git_owned"):
        git(v, "add", "-A")
    else:  # a repo the brain joined: notes and brain state only, never your other files
        git(v, "add", "-A", "--", "*.md", *[p for p in (".brain", ".gitignore") if (v.root / p).exists()])
    if git(v, "diff", "--cached", "--quiet").returncode != 0:
        c = git(v, *identity(v.root), "commit", "-q", "-m", message)
        if c.returncode != 0:
            out = (c.stdout + c.stderr).strip()
            return ("the secret scan stopped the commit: " if "secret" in out.lower() else "commit failed: ") + short(out, 160)
    return ""


def sync(v: Vault, message: str = "brain: sync") -> str:
    if not (v.root / ".git").exists():
        return "no git repo"
    problem = _commit(v, message)
    if problem:
        return problem
    names = git(v, "remote").stdout.split()
    if not names:
        return "committed (no remote)"
    name = "origin" if "origin" in names else names[0]
    url = git(v, "remote", "get-url", name).stdout.strip()
    if not v.get("allow_public_remote") and remote_visibility(url) == "PUBLIC":
        return f"committed, NOT pushed: {url} is public — make it private (or `brain config allow_public_remote true`)"
    if git(v, "rev-parse", "--abbrev-ref", "@{u}").returncode == 0:
        problem = _pull(v)
        if problem:
            return problem
    push = git(v, "push", "-q", "-u", name, "HEAD")
    return "synced" if push.returncode == 0 else "push failed: " + push.stderr.strip()[:200]


def pull(v: Vault) -> str:
    """Start from what the other machines pushed, so no rule is learned twice. Your edits of the day are committed
    first, so the rebase never has to set them aside. A conflict is left to `sync`, which reports it."""
    if git(v, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
        return "no upstream"
    problem = _commit(v, "brain: sync")
    if problem:
        return problem
    return _pull(v) or "pulled"


UNION = "*.md merge=union\n*.jsonl merge=union\n"


def _pull(v: Vault) -> str:
    """Rebase onto what the other machines pushed. '' when that worked, else the problem.

    Two laptops that both added a line to the same note conflict, and a backup that waits for a human to resolve
    it stops for good. So a conflict is retried once with git's union merge for notes and logs: both sides' lines
    stay, nothing is thrown away, and the night goes on. Only what union cannot settle still waits for a human."""
    r = git(v, "pull", "--rebase", "--autostash", "-q")
    if r.returncode == 0:
        return ""
    git(v, "rebase", "--abort")
    attrs = v.path("state", "union.gitattributes")  # .brain/state/ is per machine, never committed
    attrs.parent.mkdir(parents=True, exist_ok=True)
    attrs.write_text(UNION, encoding="utf-8")
    u = git(v, "-c", f"core.attributesFile={attrs}", "pull", "--rebase", "--autostash", "-q")
    if u.returncode == 0:
        from . import events
        events.log(v, "sync_union", detail=short(r.stderr.strip(), 200))
        return ""
    git(v, "rebase", "--abort")
    return "pull failed — resolve by hand: " + (u.stderr or r.stderr).strip()[:200]


def _locked(v: Vault) -> bool:
    """Another run is busy (the one `brain setup` starts, a catch-up and launchd at night must not overlap)."""
    import time
    lock = v.path("state", "nightly.lock")
    try:
        pid, started = lock.read_text().split()
        if os.name != "nt":  # on Windows os.kill(pid, 0) would end that process: there the age alone decides
            os.kill(int(pid), 0)
        return time.time() - float(started) < 3 * 3600
    except (OSError, ValueError):
        return False


def _lock(v: Vault) -> bool:
    """Take the lock in one step, so two runs starting in the same second cannot both get it."""
    import time
    lock = v.path("state", "nightly.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _locked(v):
                return False
            try:  # left behind by a run that died
                lock.unlink()
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {time.time()}")
        return True
    return False


def run(v: Vault, quick: bool = False) -> dict:
    if not _lock(v):
        return {"t": now_iso(), "skipped": "another nightly run is busy"}
    lock = v.path("state", "nightly.lock")
    try:
        return _run(v, quick)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _run(v: Vault, quick: bool) -> dict:
    # imported here, so the hooks that only ask `overdue()` stay fast
    from . import care, curate, distill, extract, freshness, learn, llm, maplib, metrics, outcomes, themes, trust
    from .lint import lint
    from .notes import Library
    rep: dict = {"t": now_iso(), "steps": {}, "attention": [], "broken": []}
    s = rep["steps"]
    llm.PROBLEMS.clear()
    prev = load_json(v.path("state", "nightly.json"), {})
    rep["after_broken"] = bool(isinstance(prev, dict) and prev.get("broken"))  # this run is the one retry
    wanted = v.path("state", "llm_wanted.json")
    if not llm.available(v) and wanted.exists() and not quick:  # setup found no LLM CLI, you wanted one
        found = llm.detect()
        if found:
            v.set("llm_cmd", found)
            wanted.unlink(missing_ok=True)
            s["llm_found"] = found.split()[0]

    def broken(msg: str) -> None:
        """Something failed: `brain` lists it, and the next session tells you once."""
        rep["attention"].append(msg)
        rep["broken"].append(msg)

    def step(name: str, fn):
        """Run one step; its result lands in the report. One broken step must not stop the others,
        above all not the backup at the end."""
        try:
            s[name] = fn()
        except Exception as e:  # noqa: BLE001
            s[name] = f"error: {type(e).__name__}: {e}"[:300]
            broken(f"the nightly step {name} failed → `brain doctor`")
            return None
        return s[name]

    if v.get("git_sync") and not quick and (v.root / ".git").exists():
        step("pull", lambda: pull(v))
    step("learn", lambda: learn.scan(v))
    think = llm.available(v) and not quick
    if think:
        # one tiny call first: a machine asleep or offline fails every call within a second, and the night would
        # count as done with nothing learned. Unreachable = broken, so the run is tried again after a few hours.
        ok, why = llm.reachable(v)
        s["probe"] = "ok" if ok else f"unreachable: {why}"
        think = ok  # the failed call is in llm.PROBLEMS: reported below as broken, so the night is tried again
    if not quick:  # before judge and adopt: an outcome's lesson is a draft they see, its knowledge is filed tonight
        step("outcomes", lambda: outcomes.run(v, think))
    if think:
        step("judge", lambda: learn.judge(v))
        step("merge", lambda: learn.merge(v))  # the same rule in other words, before the adoption counts sessions
    if not quick:
        # rules an agent wrote: backed by your own words they count, without evidence they are taken back. Before
        # the adoption, so the confirmed ones are part of what it sees. Nothing waits for you.
        step("settle", lambda: trust.settle(v, think))
    step("adopt", lambda: learn.adopt(v))
    if not quick:  # before distill, so a line that fell behind GitHub is corrected in the same night
        step("github", lambda: freshness.run(v))
    if think:
        step("extract", lambda: extract.run(v))
        step("distill", lambda: distill.auto(v, limit=int(v.get("distill_max"))))
    if think:
        from . import answers
        step("answers", lambda: answers.sharpen(v))  # always rules the answer check finds broken again and again
    step("curate", lambda: curate.run(v))
    step("care", lambda: care.run(v, think))
    if think:
        step("themes", lambda: themes.run(v, think))
    if not quick:
        from . import replay
        step("replay", lambda: {k: x for k, x in replay.run(v).items() if k not in ("rows", "per_lesson")})
    step("cache", lambda: metrics.refresh_cache(v))
    step("autotune", lambda: metrics.autotune(v))
    step("index_changed", lambda: maplib.write(v))

    def _lint():
        findings = lint(v, Library(v.root))
        return {"must": sum(f.level == "MUST" for f in findings), "should": sum(f.level == "SHOULD" for f in findings)}
    step("lint", _lint)
    if llm.PROBLEMS:  # a failed login looks like a quiet night unless it is said here
        rep["llm_error"] = llm.PROBLEMS[0]
        if not llm.PROBLEMS[0].startswith("today's learning budget"):
            exe = Path(shlex.split(str(v.get("llm_cmd")))[0]).name if v.get("llm_cmd") else "LLM"
            broken(f"the {exe} call failed, so nothing was learned ({short(llm.PROBLEMS[0], 80)}) → `brain doctor`")
    # Nothing here waits for you: a correction against a rule you have is settled by date and wording
    # (learn._contradiction), a claim that could not be filed is retried and then dropped (distill._failed),
    # and upkeep is fixed by care.run. All of it is in `brain review`, with undo.
    if v.get("git_sync") and not quick:
        res = step("sync", lambda: sync(v, "brain: nightly"))
        if isinstance(res, str) and not res.startswith(("synced", "committed (no remote)", "no git repo")):
            broken(f"backup: {res[:160]}")
    rep["llm"] = llm.spent(v)
    rep["llm_cmd"] = bool(v.get("llm_cmd"))  # `brain` compares: set in your shell, missing at night
    save_json(v.path("state", "nightly.json"), rep)
    return rep


def summary(rep: dict) -> str:
    """One line for `brain` and the UI: what the last run did."""
    s = rep.get("steps") or {}
    ad = s.get("adopt") if isinstance(s.get("adopt"), dict) else {}
    cu = s.get("curate") if isinstance(s.get("curate"), dict) else {}
    se = s.get("settle") if isinstance(s.get("settle"), dict) else {}
    ca = s.get("care") if isinstance(s.get("care"), dict) else {}
    th = s.get("themes") if isinstance(s.get("themes"), dict) else {}
    oc = (s.get("outcomes") or {}).get("judge") if isinstance(s.get("outcomes"), dict) else None
    oc = oc if isinstance(oc, dict) else {}
    filed = sum(1 for line in s.get("distill") or [] if isinstance(line, str) and ": " in line
                and line.split(": ", 1)[1].startswith(("append", "update", "new")))
    rules = len(ad.get("rules") or [])
    parts = [f"{rules} new rule{'s' * (rules != 1)}" if rules else "",
             f"{len(ad.get('sharpened') or []) + len(ad.get('escalated') or [])} sharpened"
             if ad.get("sharpened") or ad.get("escalated") else "",
             f"{filed} fact{'s' * (filed != 1)} filed" if filed else "",
             f"{len(cu.get('retired') or [])} retired" if cu.get("retired") else "",
             f"{len(se.get('backed') or [])} agent rule{'s' * (len(se.get('backed') or []) != 1)} confirmed by your words"
             if se.get("backed") else "",
             f"{len(se.get('discarded') or [])} without evidence taken back" if se.get("discarded") else "",
             f"{len(ca.get('fixed') or [])} tidied" if ca.get("fixed") else "",
             f"{len(th.get('joined') or [])} rule{'s' * (len(th.get('joined') or []) != 1)} joined" if th.get("joined") else "",
             f"{oc['drafts']} lesson draft{'s' * (oc['drafts'] != 1)} from outcomes" if oc.get("drafts") else "",
             "backed up" if str(s.get("sync", "")).startswith("synced") else ""]
    t = str(rep.get("t", ""))
    when = t[11:16] if days_since(t[:10]) in (0, 1) else day_label(t)
    paused = " · the day's budget was used up, the rest waits" if str(rep.get("llm_error", "")).startswith(
        "today's learning budget") else ""
    return f"{when} · " + (", ".join(x for x in parts if x) or "nothing new") + paused


# --- scheduling -----------------------------------------------------------------------------

def _cmd(v: Vault) -> list[str]:
    shim = Path(__file__).resolve().parent.parent / "brain"
    head = [sys.executable, str(shim)] if shim.is_file() else [sys.executable, "-m", "self_improving_brain"]
    return head + ["--vault", str(v.root), "nightly"]


TASK = "self-improving-brain-nightly"


def _env() -> dict:
    """What the scheduled run needs from your shell: PATH for the LLM CLI, and where your settings live if you
    moved them (XDG_*): without it the run at night would not find `llm_cmd` and learn nothing."""
    out = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    out.update({k: os.environ[k] for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME") if os.environ.get(k)})
    return out


def schedule_plan(v: Vault, hour: int = 3) -> tuple[str, str]:
    from xml.sax.saxutils import escape  # pulls in urllib: not on the path of every hook
    cmd = _cmd(v)
    log = v.path("state", "nightly.log")
    if platform.system() == "Windows":
        tr = subprocess.list2cmdline(cmd)
        return "schtasks", subprocess.list2cmdline(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", TASK,
                                                    "/ST", f"{hour:02d}:17", "/TR", tr])
    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        args = "".join(f"<string>{escape(c)}</string>" for c in cmd)
        env = "".join(f"<key>{k}</key><string>{escape(val)}</string>" for k, val in _env().items())
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>17</integer></dict>
<key>EnvironmentVariables</key><dict>{env}</dict>
<key>StandardOutPath</key><string>{escape(str(log))}</string><key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict></plist>
"""
        return str(plist), body
    env = " ".join(f"{k}={shlex.quote(val)}" for k, val in _env().items())  # cron's own PATH lacks ~/.local/bin
    line = (f"17 {hour} * * * {env + ' ' if env else ''}{' '.join(shlex.quote(c) for c in cmd)} "
            f">> {shlex.quote(str(log))} 2>&1  # {LABEL}")
    return "crontab", line


def overdue(v: Vault) -> bool:
    """The scheduled run was missed: the laptop slept or was off at night (cron and the Task Scheduler skip it then,
    launchd skips it when the Mac was off). The first session after that starts it."""
    import datetime as dt
    plan = load_json(v.path("state", "schedule.json"), None)
    if not isinstance(plan, dict) or _locked(v):  # a lock left by a crashed run does not block forever
        return False
    rep = load_json(v.path("state", "nightly.json"), {})
    rep = rep if isinstance(rep, dict) else {}
    last = str(rep.get("t") or plan.get("t") or "")
    # a night that broke (LLM login, network, backup) is tried once more after a few hours, not only tomorrow
    hours = 6 if rep.get("broken") and not rep.get("after_broken") else 26
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(last[:19])).total_seconds() > hours * 3600
    except ValueError:
        return True


def schedule_install(v: Vault, hour: int = 3) -> str:
    target, body = schedule_plan(v, hour)
    v.path("state").mkdir(parents=True, exist_ok=True)
    save_json(v.path("state", "schedule.json"), {"t": now_iso(), "hour": hour})
    if target == "schtasks":
        subprocess.run(body, check=True, capture_output=True)  # CreateProcess, no cmd.exe: & or % in a path stay text
        return f"installed Windows task {TASK} (daily at {hour:02d}:17; a missed night runs at your next session)"
    if target == "crontab":
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        lines = [l for l in cur.splitlines() if LABEL not in l] + [body]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
        return "installed cron job (a night the laptop slept through runs at your next session)"
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    subprocess.run(["launchctl", "load", str(p)], capture_output=True)
    return f"installed {p} (a night the Mac slept through runs when it wakes, or at your next session)"


def schedule_installed() -> bool:
    try:
        if platform.system() == "Windows":
            return subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True).returncode == 0
        if platform.system() == "Darwin":
            return (Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
        if shutil.which("crontab"):
            return LABEL in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        pass
    return False


def schedule_remove(v: Vault | None = None) -> str:
    if v is not None:  # no catch-up runs either
        try:
            v.path("state", "schedule.json").unlink()
        except OSError:
            pass
    if platform.system() == "Windows":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True)
        return f"removed the nightly task {TASK}" if r.returncode == 0 else "nightly schedule: none to remove"
    if platform.system() == "Darwin":
        p = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        if p.exists():
            subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
            p.unlink()
            return f"removed the nightly schedule ({p})"
        return "nightly schedule: none to remove"
    if shutil.which("crontab"):
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if LABEL in cur:  # your crontab is rewritten only when it holds the brain's line
            subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if LABEL not in l) + "\n",
                           text=True)
            return "removed the nightly cron job"
    return "nightly schedule: none to remove"
