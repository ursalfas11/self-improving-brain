"""The autonomous loop. Runs on your laptop via launchd (macOS), cron (Linux) or the Task Scheduler — no server.

  pull       what your other machines learned (if the vault syncs through git)
  learn      mine new transcripts for your corrections (no LLM)
  judge      the LLM sorts them: lasting rule, one-off steering, fact to remember, or noise   (budgeted)
  adopt      policy, no LLM: what you said twice or as "always/never" becomes a rule; a rule that comes
             back gets sharpened, then escalated
  extract    the LLM reads what is new in your sessions for decisions, facts, pitfalls       (budgeted)
  distill    files them into the notes; a newer statement replaces an outdated one           (budgeted)
  curate     quiet rules retire, the rule set stays within its caps
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
from .util import day_label, days_since, load_json, now_iso, save_json

LABEL = "dev.self-improving-brain.nightly"


def git_in(root: Path, *args: str) -> subprocess.CompletedProcess:
    # never wait for a password prompt: this runs at night, without anyone at the keyboard
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))


def git(v: Vault, *args: str) -> subprocess.CompletedProcess:
    return git_in(v.root, *args)


GITHUB = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


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


def _commit(v: Vault, message: str) -> str:
    """Commit what the brain may commit. '' when that worked, else the problem."""
    if v.get("git_owned"):
        git(v, "add", "-A")
    else:  # a repo the brain joined: notes and brain state only, never your other files
        git(v, "add", "-A", "--", "*.md", *[p for p in (".brain", ".gitignore") if (v.root / p).exists()])
    if git(v, "diff", "--cached", "--quiet").returncode != 0:
        c = git(v, "commit", "-q", "-m", message)
        if c.returncode != 0:
            return "commit failed (secret scan?): " + (c.stdout + c.stderr).strip()[:200]
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
        pull = git(v, "pull", "--rebase", "--autostash", "-q")
        if pull.returncode != 0:
            git(v, "rebase", "--abort")
            return "pull failed — resolve by hand: " + pull.stderr.strip()[:200]
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
    r = git(v, "pull", "--rebase", "--autostash", "-q")
    if r.returncode != 0:
        git(v, "rebase", "--abort")
        return "pull failed: " + r.stderr.strip()[:200]
    return "pulled"


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
    from . import curate, distill, extract, inbox, learn, llm, maplib, metrics
    from .lint import lint
    from .notes import Library
    rep: dict = {"t": now_iso(), "steps": {}, "attention": []}
    s = rep["steps"]

    def step(name: str, fn):
        """Run one step; its result lands in the report. One broken step must not stop the others,
        above all not the backup at the end."""
        try:
            s[name] = fn()
        except Exception as e:  # noqa: BLE001
            s[name] = f"error: {type(e).__name__}: {e}"[:300]
            rep["attention"].append(f"the nightly step {name} failed → `brain doctor`")
            return None
        return s[name]

    if v.get("git_sync") and not quick and (v.root / ".git").exists():
        step("pull", lambda: pull(v))
    step("learn", lambda: learn.scan(v))
    think = llm.available(v) and not quick
    if think:
        step("judge", lambda: learn.judge(v))
    step("adopt", lambda: learn.adopt(v))
    if think:
        step("extract", lambda: extract.run(v))
        step("distill", lambda: distill.auto(v, limit=int(v.get("distill_max"))))
    step("curate", lambda: curate.run(v))
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
    conflicts = [i for i in inbox.items(v) if i.get("conflict")]
    if conflicts:
        rep["attention"].append(f"{len(conflicts)} facts could not be filed on their own → `brain inbox`")
    if v.get("git_sync") and not quick:
        res = step("sync", lambda: sync(v, "brain: nightly"))
        if isinstance(res, str) and not res.startswith(("synced", "committed (no remote)", "no git repo")):
            rep["attention"].append(f"backup: {res[:160]}")
    rep["llm"] = llm.spent(v)
    save_json(v.path("state", "nightly.json"), rep)
    return rep


def summary(rep: dict) -> str:
    """One line for `brain` and the UI: what the last run did."""
    s = rep.get("steps") or {}
    ad = s.get("adopt") if isinstance(s.get("adopt"), dict) else {}
    cu = s.get("curate") if isinstance(s.get("curate"), dict) else {}
    filed = sum(1 for line in s.get("distill") or [] if isinstance(line, str) and ": " in line
                and line.split(": ", 1)[1].startswith(("append", "update", "new")))
    rules = len(ad.get("rules") or [])
    parts = [f"{rules} new rule{'s' * (rules != 1)}" if rules else "",
             f"{len(ad.get('sharpened') or []) + len(ad.get('escalated') or [])} sharpened"
             if ad.get("sharpened") or ad.get("escalated") else "",
             f"{filed} fact{'s' * (filed != 1)} filed" if filed else "",
             f"{len(cu.get('retired') or [])} retired" if cu.get("retired") else "",
             "backed up" if str(s.get("sync", "")).startswith("synced") else ""]
    t = str(rep.get("t", ""))
    when = t[11:16] if days_since(t[:10]) in (0, 1) else day_label(t)
    return f"{when} · " + (", ".join(x for x in parts if x) or "nothing new")


# --- scheduling -----------------------------------------------------------------------------

def _cmd(v: Vault) -> list[str]:
    shim = Path(__file__).resolve().parent.parent / "brain"
    head = [sys.executable, str(shim)] if shim.is_file() else [sys.executable, "-m", "self_improving_brain"]
    return head + ["--vault", str(v.root), "nightly"]


TASK = "self-improving-brain-nightly"


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
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>17</integer></dict>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(os.environ.get('PATH', '/usr/bin:/bin'))}</string></dict>
<key>StandardOutPath</key><string>{escape(str(log))}</string><key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict></plist>
"""
        return str(plist), body
    line = f"17 {hour} * * * {' '.join(shlex.quote(c) for c in cmd)} >> {shlex.quote(str(log))} 2>&1  # {LABEL}"
    return "crontab", line


def overdue(v: Vault) -> bool:
    """The scheduled run was missed: the laptop slept or was off at night (cron and the Task Scheduler skip it then,
    launchd skips it when the Mac was off). The first session after that starts it."""
    import datetime as dt
    plan = load_json(v.path("state", "schedule.json"), None)
    if not isinstance(plan, dict) or v.path("state", "nightly.lock").exists():
        return False
    last = str(load_json(v.path("state", "nightly.json"), {}).get("t") or plan.get("t") or "")
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(last[:19])).total_seconds() > 26 * 3600
    except ValueError:
        return True


def schedule_install(v: Vault, hour: int = 3) -> str:
    target, body = schedule_plan(v, hour)
    v.path("state").mkdir(parents=True, exist_ok=True)
    save_json(v.path("state", "schedule.json"), {"t": now_iso(), "hour": hour})
    if target == "schtasks":
        subprocess.run(body, check=True, shell=True, capture_output=True)
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
        return f"removed Windows task {TASK}" if r.returncode == 0 else "nothing installed"
    if platform.system() == "Darwin":
        p = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        if p.exists():
            subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
            p.unlink()
            return f"removed {p}"
        return "nothing installed"
    if shutil.which("crontab"):
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if LABEL not in l) + "\n", text=True)
        return "removed cron job"
    return "nothing installed"
