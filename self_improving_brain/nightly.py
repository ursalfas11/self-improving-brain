"""The autonomous loop. Runs on your laptop via launchd (macOS) or cron (Linux) — no server.

  learn      mine new transcripts for corrections -> lesson drafts (refined by llm_cmd if set)
  distill    inbox -> wiki (only with llm_cmd; otherwise your agent does it on `brain distill`)
  cache      rebuild the learned recall model from events
  autotune   adjust the recall threshold from measured precision
  health     flag lessons that are ignored, never triggered or dormant
  index      regenerate INDEX.md
  lint       MUST findings go into the next session start
  sync       git commit + pull --rebase + push (if the vault is a git repo with a remote)

Nothing here ever deletes knowledge or approves a lesson. That stays your call.
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
from xml.sax.saxutils import escape

from . import distill, inbox, learn, llm, maplib, metrics
from .config import Vault
from .lint import lint
from .notes import Library
from .util import now_iso, save_json

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


def sync(v: Vault, message: str = "brain: sync") -> str:
    if not (v.root / ".git").exists():
        return "no git repo"
    if v.get("git_owned"):
        git(v, "add", "-A")
    else:  # a repo the brain joined: notes and brain state only, never your other files
        git(v, "add", "-A", "--", "*.md", *[p for p in (".brain", ".gitignore") if (v.root / p).exists()])
    if git(v, "diff", "--cached", "--quiet").returncode != 0:
        c = git(v, "commit", "-q", "-m", message)
        if c.returncode != 0:
            return "commit failed (secret scan?): " + (c.stdout + c.stderr).strip()[:200]
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


def run(v: Vault, quick: bool = False) -> dict:
    rep: dict = {"t": now_iso(), "steps": {}, "attention": []}
    s = rep["steps"]

    def step(name: str, fn):
        """Run one step; its result lands in the report. One broken step must not stop the others,
        above all not the backup at the end."""
        try:
            s[name] = fn()
        except Exception as e:  # noqa: BLE001
            s[name] = f"error: {type(e).__name__}: {e}"[:300]
            rep["attention"].append(f"nightly step {name} failed → `brain nightly` shows why")
            return None
        return s[name]

    step("learn", lambda: learn.scan(v))
    if llm.available(v) and not quick:
        def _distill():
            try:
                return distill.auto(v)
            except llm.NoLLM as e:
                return str(e)
        step("distill", _distill)
    step("cache", lambda: metrics.refresh_cache(v))
    step("autotune", lambda: metrics.autotune(v))
    health = step("lessons_needing_care",
                  lambda: [h for h in metrics.lesson_health(v, Library(v.root)) if h["verdict"] != "ok"])
    if health:
        rep["attention"].append(f"{len(health)} lessons need care → `brain stats`")
    step("index_changed", lambda: maplib.write(v))

    def _lint():
        findings = lint(v, Library(v.root))
        return {"must": sum(f.level == "MUST" for f in findings), "should": sum(f.level == "SHOULD" for f in findings)}
    step("lint", _lint)
    conflicts = [i for i in inbox.items(v) if i.get("conflict")]
    if conflicts:
        rep["attention"].append(f"{len(conflicts)} inbox conflicts wait for your decision → `brain inbox`")
    if v.get("git_sync") and not quick:
        res = step("sync", lambda: sync(v, "brain: nightly"))
        if isinstance(res, str) and not res.startswith(("synced", "committed (no remote)", "no git repo")):
            rep["attention"].append(f"backup: {res[:160]}")
    save_json(v.path("state", "nightly.json"), rep)
    return rep


# --- scheduling -----------------------------------------------------------------------------

def _cmd(v: Vault) -> list[str]:
    shim = Path(__file__).resolve().parent.parent / "brain"
    head = [sys.executable, str(shim)] if shim.is_file() else [sys.executable, "-m", "self_improving_brain"]
    return head + ["--vault", str(v.root), "nightly"]


TASK = "self-improving-brain-nightly"


def schedule_plan(v: Vault, hour: int = 3) -> tuple[str, str]:
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


def schedule_install(v: Vault, hour: int = 3) -> str:
    target, body = schedule_plan(v, hour)
    v.path("state").mkdir(parents=True, exist_ok=True)
    if target == "schtasks":
        subprocess.run(body, check=True, shell=True, capture_output=True)
        return f"installed Windows task {TASK} (daily at {hour:02d}:17 while the PC is on)"
    if target == "crontab":
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        lines = [l for l in cur.splitlines() if LABEL not in l] + [body]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
        return "installed cron job (missed runs while the laptop sleeps are skipped; launchd/anacron catch up)"
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    subprocess.run(["launchctl", "load", str(p)], capture_output=True)
    return f"installed {p} (launchd runs a missed job when the Mac wakes up)"


def schedule_remove() -> str:
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
