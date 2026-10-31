"""`brain setup`: one command, no questions, about thirty seconds. Then nothing to do.

  vault      ~/brain (or the one you have), with its own git history and a secret scan before every commit
  agents     every agent found here: Claude Code hooks, Codex and Gemini instructions, MCP for the editors
  command    `brain` on your PATH
  history    reads the sessions you already had: your baseline, and the first rules tonight
  learning   the LLM CLI you already use (claude, codex, ollama) with your login, under a daily budget
  nightly    the learning run, every night on this machine
  backup     only with --github: a PRIVATE repo, pushed every night. Nothing public is ever created.

Everything else stays on this machine. `brain uninstall` takes it all out again.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import adapters, learn, llm, metrics, nightly
from .config import candidate_roots, is_vault, open_vault
from .util import load_json

PKG_ROOT = Path(__file__).resolve().parent.parent
NAMES = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "claude-desktop": "Claude Desktop",
         "cursor": "Cursor", "windsurf": "Windsurf", "vscode": "VS Code"}


class Out:
    def __init__(self):
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.todo: list[str] = []

    def c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def row(self, mark: str, label: str, text: str) -> None:
        sym = {"ok": self.c("32", "✓"), "skip": self.c("2", "·"), "warn": self.c("33", "!")}[mark]
        print(f"  {sym} {label:<10} {text}")
        if mark == "warn":
            self.todo.append(text)


def default_vault() -> Path:
    # an existing brain first ($BRAIN_VAULT, the saved one, ~/brain): re-running setup must never start a second one
    for cand in candidate_roots():
        if is_vault(cand):
            return cand
    home = Path.home()
    for cand in (home / "brain", home / "agent-brain", home / "self-improving-brain-vault"):
        if not cand.exists() or not any(cand.iterdir()):
            return cand
    return home / "self-improving-brain-vault"


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _home(p: Path) -> str:
    h = str(Path.home())
    return "~" + str(p)[len(h):] if str(p).startswith(h + os.sep) else str(p)


# --- steps -----------------------------------------------------------------------------------

def make_vault(o: Out, path: str | None) -> Path | None:
    from . import cli
    root = (Path(path).expanduser() if path else default_vault()).resolve()
    if is_vault(root):
        o.row("ok", "Vault", f"{_home(root)} — the one you have")
        return root
    if (root / ".brain").exists():
        o.row("warn", "Vault", f"{root}/.brain belongs to another tool — choose another folder: brain setup --vault PATH")
        return None
    # a folder you named that already holds notes: the brain moves in beside them, nothing is overwritten
    force = root.exists() and any(p for p in root.iterdir() if not p.name.startswith("."))
    if cli.main(["init", str(root), "--git", "--quiet"] + (["--force"] if force else [])):
        return None
    owned = open_vault(str(root)).get("git_owned")  # only a repo the brain created gets everything committed
    if owned and shutil.which("git") and git(root, "rev-parse", "HEAD").returncode != 0:
        git(root, "add", "-A")
        git(root, *nightly.identity(root), "commit", "-q", "-m", "brain: init")
    o.row("ok", "Vault", f"{_home(root)} — " + ("added to your notes" if force else "new, with its own git history"))
    return root


AGENTS = [
    ("claude", lambda: shutil.which("claude") or (Path.home() / ".claude").is_dir()),
    ("codex", lambda: shutil.which("codex") or (Path.home() / ".codex").is_dir()),
    ("gemini", lambda: shutil.which("gemini") or (Path.home() / ".gemini").is_dir()),
]
# editors without instruction files of their own get the brain as MCP tools; CLI agents above get instructions
MCP_CLIENTS = ("claude-desktop", "cursor", "windsurf", "vscode")


def connect_agents(o: Out, v, only: list[str] | None) -> list[str]:
    from . import mcp
    done, failed = [], []
    for key, present in AGENTS:
        if (only is not None and key not in only) or not present():
            continue
        if key == "claude" and adapters.from_plugin():
            done.append(key)
            continue
        try:
            adapters.install(v, key)
            done.append(key)
        except SystemExit as e:
            failed.append(f"{NAMES[key]}: {e}")
    for c in mcp.detected():
        if c not in MCP_CLIENTS or (only is not None and c not in only and "mcp" not in only):
            continue
        try:
            mcp.install(v, c)
            done.append(c)
        except (ValueError, OSError) as e:
            failed.append(f"{NAMES[c]}: {e}")
    if done:
        o.row("ok", "Agents", " · ".join(NAMES[k] for k in done))
    else:
        o.row("skip", "Agents", "none found — for any other agent: `brain install agents` in a project, or `brain mcp config`")
    for f in failed:
        o.row("warn", "", f)
    return done


def link_command(o: Out) -> None:
    if adapters.cli_name() == "brain":
        o.row("ok", "Command", "brain")
        return
    shim = PKG_ROOT / "brain"
    src = shim if shim.is_file() else adapters.console_script()
    if os.name == "nt" or src is None:
        o.row("skip", "Command", f"run it as {adapters.brain_cmd()}")
        return
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    target = bindir / "brain"
    if target.exists() or target.is_symlink():
        if not (target.is_symlink() and target.resolve() == src.resolve()):
            o.row("warn", "Command", f"{_home(target)} is something else — left alone; use {adapters.brain_cmd()}")
            return
    else:
        target.symlink_to(src)
    if str(bindir) not in os.environ.get("PATH", "").split(os.pathsep):
        rc = "zshrc" if "zsh" in os.environ.get("SHELL", "") else "bashrc"
        o.row("warn", "Command", f"brain → {_home(target)}; put it on your PATH: "
                                 f"echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.{rc}")
    else:
        found = shutil.which("brain")
        if found and Path(found).resolve() != src.resolve():
            o.row("warn", "Command", f"another `brain` comes first on your PATH ({found}) — use {adapters.brain_cmd()}")
        else:
            o.row("ok", "Command", f"brain → {_home(target)}")


def learn_history(o: Out, v) -> int:
    files = learn.transcript_files(v)
    if not files:
        o.row("skip", "History", "no past sessions yet — it starts learning with your next one")
        return 0
    learn.scan(v, max_age_days=60)
    day = metrics.installed(v)  # from here on, a change is the brain's; before, it is your history
    curve = metrics.weekly_curve(v, 104)
    messages, mistakes = sum(r["messages"] for r in curve), sum(r["corrections"] for r in curve)
    base = metrics.since_start(curve, installed=day)
    per100 = base["base"] if base else (100 * mistakes / messages if messages else 0.0)
    sessions = metrics.session_count(v)  # all it has read, also on a second `brain setup`
    o.row("ok", "History", f"{sessions:,} session{'s' * (sessions != 1)} · {messages:,} of your messages · "
                           f"{per100:.1f} mistakes per 100 (your baseline)")
    return sessions


def choose_llm(o: Out, v, allow: bool) -> bool:
    if not allow:
        o.row("skip", "Learning", "off (--no-llm): the brain measures, but learns nothing new")
        return False
    if v.get("llm_cmd"):
        cmd = str(v.get("llm_cmd"))
    else:
        cmd = llm.detect()
        if not cmd:
            o.row("warn", "Learning", "no LLM CLI found (claude, codex, ollama) — install one, then run brain setup again")
            return False
        v.set("llm_cmd", cmd)
    model = " (Haiku)" if " haiku" in cmd else ""
    how = "on this machine" if cmd.split()[0] == "ollama" else "with your login"
    o.row("ok", "Learning", f"{cmd.split()[0]}{model} {how} · at most ${float(v.get('llm_budget_usd')):.2f} a day")
    return True


def schedule(o: Out, v, allow: bool) -> bool:
    if not allow:
        o.row("skip", "Nightly", "not scheduled (--no-schedule) — `brain nightly` runs it by hand")
        return False
    try:
        nightly.schedule_install(v)
    except (OSError, subprocess.CalledProcessError) as e:
        o.row("warn", "Nightly", f"could not schedule it: {e} — try `brain schedule install`")
        return False
    o.row("ok", "Nightly", "every night at 03:17 (a night the laptop slept through runs at your next session)")
    return True


def gh(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    # always github.com: with only a company GHE login, gh would otherwise put your brain on that server
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, GH_HOST="github.com"))


def gh_login() -> str:
    if not shutil.which("gh"):
        return ""
    try:
        if gh("auth", "status", "--hostname", "github.com").returncode != 0:
            return ""
        r = gh("api", "user", "-q", ".login")
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_exists(full: str) -> bool:
    return gh("repo", "view", full).returncode == 0


def _push_works(root: Path) -> bool:
    """The nightly run pushes without anyone at the keyboard: prove that works now."""
    if nightly.git_in(root, "push", "-q", "-u", "origin", "HEAD").returncode == 0:
        return True
    url = nightly.git_in(root, "remote", "get-url", "origin").stdout.strip()
    if url.startswith("https://github.com/") and shutil.which("gh") and gh("auth", "setup-git", "--hostname", "github.com").returncode == 0:
        return nightly.git_in(root, "push", "-q", "-u", "origin", "HEAD").returncode == 0
    return False


def backup(o: Out, v, create: bool) -> None:
    root = v.root
    if not (root / ".git").exists():
        o.row("skip", "Backup", "the vault is not a git repo — `git init` in it enables backups")
        return
    url, problem = nightly.backup_state(root)
    if url:
        vis = nightly.remote_visibility(url)
        if vis == "PUBLIC" and not v.get("allow_public_remote"):
            v.set("git_sync", False)
            o.row("warn", "Backup", f"{url} is PUBLIC — anyone could read your brain. Sync is off until it is private.")
        elif not v.get("git_sync"):
            o.row("skip", "Backup", f"{url} is yours; the brain pushes there once you say so: brain config git_sync true")
        elif problem:
            o.row("warn", "Backup", f"{url}: {problem} — `brain sync` (check your git login if it fails)")
        else:
            o.row("ok", "Backup", f"{url}{' (private)' if vis == 'PRIVATE' else ''} — every night")
        return
    if not create:
        o.row("skip", "Backup", "none — `brain setup --github` keeps a private copy on GitHub")
        return
    login = gh_login()
    if not login:
        o.row("warn", "Backup", "needs the GitHub CLI, logged in: gh auth login, then brain setup --github")
        return
    name = root.name if root.name not in ("", ".") else "brain"
    if repo_exists(f"{login}/{name}"):
        name = f"{name}-vault" if not repo_exists(f"{login}/{name}-vault") else f"{name}-vault-{os.getpid()}"
    full = f"{login}/{name}"
    try:
        r = gh("repo", "create", full, "--private", "--source", str(root), "--remote", "origin",
               "--push", "--description", "My self-improving brain (private)", timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        r = subprocess.CompletedProcess([], 1, "", str(e))
    url = nightly.git_in(root, "remote", "get-url", "origin").stdout.strip()
    if not url:
        o.row("warn", "Backup", "could not create it: " + (r.stderr or r.stdout).strip()[:200])
        return
    if nightly.remote_visibility(url) != "PRIVATE":
        v.set("git_sync", False)
        o.row("warn", "Backup", f"{full} does not report itself as private — sync stays off. Make it private, then: "
                                "brain config git_sync true")
        return
    v.set("git_sync", True)
    if _push_works(root):
        o.row("ok", "Backup", f"github.com/{full} (private) — every night")
    else:
        o.row("warn", "Backup", f"created github.com/{full} (private), but pushing failed — fix your git login, then: "
                                f"git -C {root} push -u origin HEAD")


def first_run(v) -> None:
    """Learn from the history right away instead of waiting for the night."""
    subprocess.Popen([sys.executable, "-m", "self_improving_brain", "--vault", str(v.root), "nightly"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(PKG_ROOT))


def run(args: argparse.Namespace) -> int:
    o = Out()
    print("\n  🧠  " + o.c("1", "self-improving-brain") + "\n")
    root = make_vault(o, args.vault)
    if root is None:
        return 1
    v = open_vault(str(root))
    connect_agents(o, v, args.agents.split(",") if args.agents else None)
    link_command(o)
    sessions = learn_history(o, v)
    thinks = choose_llm(o, v, not args.no_llm)
    schedule(o, v, not args.no_schedule)
    backup(o, v, bool(getattr(args, "github", False)))
    last = load_json(v.path("state", "nightly.json"), {})
    # the first run, or the first since learning was switched on (setup once more after installing an LLM CLI)
    if thinks and sessions and not last.get("llm_cmd") and not os.environ.get("BRAIN_SETUP_NO_RUN"):
        first_run(v)
        print("\n  Done. It is learning from your history right now; the first rules are in place in a few minutes.")
    else:
        print("\n  Done.")
    left = len(o.todo)
    print(("  Nothing else to do. " if not left else "  One thing above (!) needs you. " if left == 1
           else f"  {left} things above (!) need you. ") + o.c("1", "brain") + " shows how it is doing.\n")
    return 0
