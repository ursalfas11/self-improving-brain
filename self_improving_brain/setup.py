"""`brain setup`: from zero to a working, backed-up, self-improving brain in one command.

Steps (each one asks, `--yes` accepts the defaults):
  1. vault        create it (with its own git history and the secret-scan pre-commit hook)
  2. agents       connect every agent found on this machine
  3. command      put `brain` on your PATH
  4. history      learn from the sessions you already had — lessons and a baseline from day one
  5. llm          optionally let an LLM CLI you already use judge lesson candidates at night
  6. backup       recommend (and create) a PRIVATE GitHub repo, so a dead laptop never kills your brain
  7. nightly      schedule the self-improvement loop on this machine

Nothing public is ever created. Nothing leaves the machine unless you say yes to the backup.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import adapters, learn, llm, nightly
from .config import candidate_roots, is_vault, open_vault

PKG_ROOT = Path(__file__).resolve().parent.parent
B, D, G, Y, R, X = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"


class Asker:
    def __init__(self, assume_yes: bool):
        self.yes = assume_yes
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def c(self, code: str, text: str) -> str:
        return f"{code}{text}{X}" if self.color else text

    def ask(self, question: str, default: bool = True) -> bool:
        if self.yes:
            print(f"{question} {'yes' if default else 'no'} (default)")
            return default
        hint = "[Y/n]" if default else "[y/N]"
        try:
            ans = input(f"{question} {hint} ").strip().lower()
        except EOFError:
            return default
        return default if not ans else ans in ("y", "yes", "j", "ja")

    def text(self, question: str, default: str) -> str:
        if self.yes:
            return default
        try:
            ans = input(f"{question} [{default}] ").strip()
        except EOFError:
            return default
        return ans or default


def step(a: Asker, n: int, title: str) -> None:
    print("\n" + a.c(B, f"{n}/8  {title}"))


def ok(a: Asker, msg: str) -> None:
    print("   " + a.c(G, "✓ ") + msg)


def note(a: Asker, msg: str) -> None:
    print("   " + a.c(D, msg))


def warn(a: Asker, msg: str) -> None:
    print("   " + a.c(Y, "! ") + msg)


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


# --- steps -----------------------------------------------------------------------------------

def make_vault(a: Asker, path: str | None) -> Path | None:
    from . import cli
    root = Path(path).expanduser() if path else Path(a.text("   Where should your brain live?", str(default_vault()))).expanduser()
    root = root.resolve()
    if is_vault(root):
        ok(a, f"using existing vault {root}")
        return root
    if (root / ".brain").exists():
        warn(a, f"{root}/.brain belongs to another tool — choose a different folder (brain setup --vault PATH)")
        return None
    force = root.exists() and any(p for p in root.iterdir() if not p.name.startswith("."))
    if force and not a.ask(f"   {root} already has files. Add the brain to them (nothing is overwritten)?", True):
        return None
    rc = cli.main(["init", str(root), "--git", "--quiet"] + (["--force"] if force else []))
    if rc:
        return None
    owned = open_vault(str(root)).get("git_owned")  # only a repo the brain created gets everything committed
    if owned and shutil.which("git") and git(root, "rev-parse", "HEAD").returncode != 0:
        git(root, "add", "-A")
        identity = [] if git(root, "config", "user.email").stdout.strip() else \
            ["-c", "user.name=brain", "-c", "user.email=brain@localhost"]
        git(root, *identity, "commit", "-q", "-m", "brain: init")
    return root


AGENTS = [
    ("claude", "Claude Code", "native hooks + /brain-review, /brain-distill, /brain-ingest, /brain-ask",
     lambda: shutil.which("claude") or (Path.home() / ".claude").is_dir()),
    ("codex", "Codex CLI", "instructions in ~/.codex/AGENTS.md + learning from ~/.codex/sessions",
     lambda: shutil.which("codex") or (Path.home() / ".codex").is_dir()),
    ("gemini", "Gemini CLI", "instructions in ~/.gemini/GEMINI.md",
     lambda: shutil.which("gemini") or (Path.home() / ".gemini").is_dir()),
]


def connect_agents(a: Asker, v, only: list[str] | None) -> list[str]:
    done = []
    for key, name, what, present in AGENTS:
        if only is not None and key not in only:
            continue
        if not present():
            continue
        if a.ask(f"   Connect {name}? ({what})", True):
            try:
                adapters.install(v, key)
                ok(a, f"{name} connected")
                done.append(key)
            except SystemExit as e:
                warn(a, str(e))
    if not done:
        note(a, "no agent connected. Cursor, Aider, Grok CLI & co: run `brain install agents` inside a project")
        note(a, "(writes the brain block into that project's AGENTS.md)")
    return done


def link_command(a: Asker) -> None:
    if adapters.cli_name() == "brain":
        ok(a, f"`brain` is on your PATH ({shutil.which('brain')})")
        return
    shim = PKG_ROOT / "brain"
    src = shim if shim.is_file() else adapters.console_script()
    if os.name == "nt" or src is None:
        note(a, f"run it as {adapters.brain_cmd()} <command>")
        return
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    target = bindir / "brain"
    if target.is_symlink() and target.resolve() == src.resolve():
        ok(a, f"already linked {target}")
    elif target.exists() or target.is_symlink():
        warn(a, f"{target} exists and is something else — left untouched; use {adapters.brain_cmd()}")
        return
    else:
        target.symlink_to(src)
        ok(a, f"linked {target}")
    found = shutil.which("brain")
    if str(bindir) not in os.environ.get("PATH", "").split(os.pathsep):
        warn(a, f'add it to your PATH:  echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.{"zshrc" if "zsh" in os.environ.get("SHELL", "") else "bashrc"}')
    elif found and Path(found).resolve() != src.resolve():
        warn(a, f"another `brain` comes first on your PATH ({found}) — use {adapters.brain_cmd()}")


def learn_history(a: Asker, v) -> int:
    files = learn.transcript_files(v)
    if not files:
        note(a, "no past sessions found yet — the brain starts learning with your next one")
        return 0
    note(a, f"reading {len(files)} session transcripts (stays on this machine)…")
    st = learn.scan(v, max_age_days=60)
    gs = learn.groups(learn.drafts(v))
    per100 = 100 * st["corrections"] / st["user_messages"] if st["user_messages"] else 0
    ok(a, f"{st['transcripts']} sessions · {st['user_messages']} of your messages · "
          f"{st['corrections']} corrections ({per100:.1f} per 100 messages) → {len(gs)} lesson candidates")
    for g in gs[:3]:
        note(a, f"· “{g[0]['quote'][:90].strip()}”" + (f"  (seen {len(g)}×)" if len(g) > 1 else ""))
    if per100:
        note(a, "that rate is your baseline — `brain stats` shows whether it goes down from here")
    return len(gs)


def add_packs(a: Asker, v, allow: bool) -> None:
    from . import packs
    have = {p["name"] for p in packs.listing(v) if p["installed"]}
    want = [n for n in ("safety", "secrets") if n not in have]
    if not allow or not want:
        return
    if not a.ask("Add the safety and secrets lesson packs? (the agent asks before rm -rf, DROP TABLE, publishing;"
                 " no keys in code)", True):
        note(a, "later: `brain pack list`")
        return
    for n in want:
        try:
            p = packs.add(v, n)
            ok(a, f"pack {n}: {len(p['lessons'])} lessons")
        except ValueError as e:
            warn(a, f"pack {n}: {e}")
    note(a, "more packs: testing, git, python, typescript → `brain pack list`")


def choose_llm(a: Asker, v, candidates: int, allow: bool) -> None:
    if not allow:
        note(a, "skipped")
        return
    if v.get("llm_cmd"):
        ok(a, f"already set: {v.get('llm_cmd')}")
        return
    cmd = llm.detect()
    if not cmd:
        note(a, "no LLM CLI found (claude, codex, ollama). Your agent judges candidates during /brain-review instead.")
        return
    tool = cmd.split()[0]
    print(f"   Found {a.c(B, tool)}. At night it can sort real mistakes from noise and write the rule for each,")
    print("   so your review is a quick yes/no. It uses your existing CLI login; no API key is stored here.")
    if a.ask(f"   Use `{tool}` for that?", True):
        v.set("llm_cmd", cmd)
        ok(a, f"llm_cmd = {cmd}")
        if candidates:
            subprocess.Popen([sys.executable, "-m", "self_improving_brain", "--vault", str(v.root), "refine"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             start_new_session=True, cwd=str(PKG_ROOT))
            note(a, f"judging your {candidates} candidates in the background (`brain review` shows the result)")
    else:
        note(a, "fine — your agent does the judging during /brain-review")


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


def _push_works(a: Asker, root: Path) -> bool:
    """The nightly run pushes without anyone at the keyboard: prove that works now."""
    if nightly.git_in(root, "push", "-q", "-u", "origin", "HEAD").returncode == 0:
        return True
    url = nightly.git_in(root, "remote", "get-url", "origin").stdout.strip()
    if url.startswith("https://github.com/") and shutil.which("gh") and gh("auth", "setup-git", "--hostname", "github.com").returncode == 0:
        note(a, "git now uses your gh login for github.com (gh auth setup-git)")
        return nightly.git_in(root, "push", "-q", "-u", "origin", "HEAD").returncode == 0
    return False


def backup(a: Asker, v, allow: bool) -> None:
    root = v.root
    if not (root / ".git").exists():
        warn(a, "the vault is not a git repo — run `git init` in it to enable backups")
        return
    url, problem = nightly.backup_state(root)
    if url:
        vis = nightly.remote_visibility(url) if allow else ""
        if vis == "PUBLIC" and not v.get("allow_public_remote"):
            v.set("git_sync", False)
            warn(a, f"{url} is PUBLIC — anyone can read your brain there. Nightly sync is off.")
            note(a, "make the repo private on GitHub, then:  brain config git_sync true")
            return
        if not v.get("git_sync"):
            # a repo the brain joined: syncing commits your notes and pushes them to that remote
            if a.ask(f"   This vault already has the remote {url}{' (private)' if vis == 'PRIVATE' else ''}."
                     " Commit your notes and push them there every night?", vis == "PRIVATE"):
                v.set("git_sync", True)
            else:
                note(a, "nightly sync stays off — turn it on any time:  brain config git_sync true")
                return
        if problem:
            warn(a, f"backup remote {url} is set, but {problem} — run `brain sync` (check your git login if it fails)")
        else:
            ok(a, f"backed up to {url}" + (" (private)" if vis == "PRIVATE" else "") + " — synced every night")
        return
    print("   Your brain grows every day. If this laptop dies, it should not die with it.")
    print("   " + a.c(B, "Recommended:") + " keep it in a PRIVATE git repo — the nightly run pushes there automatically.")
    login = gh_login() if allow else ""
    if login:
        name = root.name if root.name not in ("", ".") else "brain"
        if repo_exists(f"{login}/{name}"):
            name = f"{name}-vault" if not repo_exists(f"{login}/{name}-vault") else f"{name}-vault-{os.getpid()}"
        full = f"{login}/{name}"
        if a.ask(f"   Create the PRIVATE repo github.com/{full} now and push your brain there?", True):
            try:
                r = gh("repo", "create", full, "--private", "--source", str(root), "--remote", "origin",
                       "--push", "--description", "My self-improving brain (private)", timeout=180)
            except (OSError, subprocess.SubprocessError) as e:
                r = subprocess.CompletedProcess([], 1, "", str(e))
            url = nightly.git_in(root, "remote", "get-url", "origin").stdout.strip()
            if not url:
                warn(a, "could not create it: " + (r.stderr or r.stdout).strip()[:200])
            else:
                vis = nightly.remote_visibility(url)
                if vis != "PRIVATE":
                    v.set("git_sync", False)
                    warn(a, f"{full} reports visibility {vis or '?'} — sync stays off. Make it private on GitHub, "
                            "then: brain config git_sync true")
                    return
                v.set("git_sync", True)
                if _push_works(a, root):
                    ok(a, f"backed up to github.com/{full} (private) — synced every night")
                else:
                    warn(a, f"created github.com/{full} (private), but pushing from this machine failed.")
                    note(a, f"fix your git login for github.com, then:  git -C {root} push -u origin HEAD")
                return
    print("   Do it later with one of these:")
    print(f"     gh repo create my-brain --private --source {root} --remote origin --push")
    print(f"     git -C {root} remote add origin <your-private-git-url> && git -C {root} push -u origin main")


def schedule(a: Asker, v, allow: bool) -> None:
    if not allow:
        note(a, "skipped — run `brain schedule install` any time")
        return
    print("   Every night: learn from new sessions, tune recall, check lessons, rebuild the index, back up.")
    if a.ask("   Schedule it on this machine?", True):
        try:
            ok(a, nightly.schedule_install(v))
        except (OSError, subprocess.CalledProcessError) as e:
            warn(a, f"could not schedule: {e}. Run `brain nightly` by hand or try `brain schedule install`.")


def run(args: argparse.Namespace) -> int:
    interactive = sys.stdin.isatty()
    if not interactive and not args.yes:
        print("brain setup asks a few questions. Run it in a terminal, or pass --yes to accept the defaults:\n"
              "  vault ~/brain · connect detected agents · learn from past sessions · safety+secrets packs ·\n"
              "  use a detected LLM CLI ·\n"
              "  create a PRIVATE GitHub backup repo (if gh is logged in) · schedule the nightly run")
        return 2
    a = Asker(args.yes)
    print(a.c(B, "\n🧠  self-improving-brain setup") + a.c(D, "  — everything stays on this machine unless you say otherwise"))

    step(a, 1, "Your vault")
    root = make_vault(a, args.vault)
    if root is None:
        return 1
    v = open_vault(str(root))
    ok(a, f"vault ready at {root}")

    step(a, 2, "Connect your agents")
    connect_agents(a, v, args.agents.split(",") if args.agents else None)

    step(a, 3, "The `brain` command")
    link_command(a)

    step(a, 4, "Learn from the sessions you already had")
    candidates = learn_history(a, v)

    step(a, 5, "Lessons other people already learned")
    add_packs(a, v, not getattr(args, "no_packs", False))

    step(a, 6, "Smarter judging at night (optional)")
    choose_llm(a, v, candidates, not args.no_llm)

    step(a, 7, "Backup")
    backup(a, v, not args.no_github)

    step(a, 8, "Nightly self-improvement")
    schedule(a, v, not args.no_schedule)

    print("\n" + a.c(B, "Done.") + " Open a new agent session — the brain is in it from the first prompt.")
    if candidates:
        print(f"Start with {a.c(B, '/brain-review')} (Claude Code) or `brain review` to turn your history into lessons.")
    print(a.c(D, "Check everything any time with `brain doctor`. Undo with `brain uninstall <agent>` and `brain schedule remove`."))
    return 0
