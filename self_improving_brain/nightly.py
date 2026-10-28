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


def git(v: Vault, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(v.root), *args], capture_output=True, text=True)


def sync(v: Vault, message: str = "brain: sync") -> str:
    if not (v.root / ".git").exists():
        return "no git repo"
    git(v, "add", "-A")
    if git(v, "diff", "--cached", "--quiet").returncode != 0:
        c = git(v, "commit", "-q", "-m", message)
        if c.returncode != 0:
            return "commit failed (secret scan?): " + (c.stdout + c.stderr).strip()[:200]
    if not git(v, "remote").stdout.strip():
        return "committed (no remote)"
    pull = git(v, "pull", "--rebase", "--autostash", "-q")
    if pull.returncode != 0:
        git(v, "rebase", "--abort")
        return "pull failed — resolve by hand: " + pull.stderr.strip()[:200]
    push = git(v, "push", "-q")
    return "synced" if push.returncode == 0 else "push failed: " + push.stderr.strip()[:200]


def run(v: Vault, quick: bool = False) -> dict:
    rep: dict = {"t": now_iso(), "steps": {}, "attention": []}
    s = rep["steps"]
    s["learn"] = learn.scan(v)
    if llm.available(v) and not quick:
        try:
            s["distill"] = distill.auto(v)
        except llm.NoLLM as e:
            s["distill"] = str(e)
    metrics.refresh_cache(v)
    tuned = metrics.autotune(v)
    if tuned:
        s["autotune"] = tuned
    lib = Library(v.root)
    health = [h for h in metrics.lesson_health(v, lib) if h["verdict"] != "ok"]
    s["lessons_needing_care"] = health
    if health:
        rep["attention"].append(f"{len(health)} lessons need care → `brain stats`")
    s["index_changed"] = maplib.write(v)
    findings = lint(v, Library(v.root))
    s["lint"] = {"must": sum(f.level == "MUST" for f in findings), "should": sum(f.level == "SHOULD" for f in findings)}
    conflicts = [i for i in inbox.items(v) if i.get("conflict")]
    if conflicts:
        rep["attention"].append(f"{len(conflicts)} inbox conflicts wait for your decision → `brain inbox`")
    if v.get("git_sync") and not quick:
        s["sync"] = sync(v, "brain: nightly")
    save_json(v.path("state", "nightly.json"), rep)
    return rep


# --- scheduling -----------------------------------------------------------------------------

def _cmd(v: Vault) -> list[str]:
    shim = Path(__file__).resolve().parent.parent / "brain"
    head = [sys.executable, str(shim)] if shim.is_file() else [sys.executable, "-m", "self_improving_brain"]
    return head + ["--vault", str(v.root), "nightly"]


def schedule_plan(v: Vault, hour: int = 3) -> tuple[str, str]:
    cmd = _cmd(v)
    log = v.path("state", "nightly.log")
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
