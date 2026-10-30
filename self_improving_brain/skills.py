"""Skills the brain writes itself: procedures your agent repeated across sessions.

`brain skills mine` reads your transcripts for runs of shell commands that came back in at least
three sessions (a release, a deploy, the way a project is tested), keeps the longest form of each,
and remembers which prompt started it. Nothing is installed until you approve it:

  brain skills approve <id> --name release --description "Cut a release of this package"

writes the procedure as a note in your vault (`30-resources/skills/skill-<name>.md`, the one
truth), adds a `skill` lesson that points the agent at it when the prompt sounds like that task,
and installs a Claude Code skill (`~/.claude/skills/brain-<name>/SKILL.md`). `brain skills sync`
reinstalls every approved skill from the vault, e.g. on a new machine.
"""
from __future__ import annotations

import json
import re
import shlex
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import learn, secrets, trust
from .config import Vault
from .hooks import command_head
from .notes import Lesson, Library
from .transcripts import parse
from .util import digest, now_iso, read_jsonl, short, slugify, terms, today, write_jsonl

MIN_SESSIONS = 3
READ_ONLY = {"ls", "cat", "pwd", "echo", "cd", "head", "tail", "wc", "which", "true", "clear", "grep", "rg", "find",
             "sed", "awk", "less", "more", "file", "stat", "du", "df", "tree", "sleep", "date", "env", "printenv",
             "jq", "sort", "uniq", "cut", "tr", "diff", "open", "type", "test", "[", "export", "source", "set"}
SHELL = {"for", "do", "done", "then", "fi", "if", "elif", "else", "case", "esac", "while", "until", "{", "}", "(", ")",
         "function", "exit", "break", "continue", "return", "local", "read", "printf", "shift", "wait", "trap", "!"}
INTERPRETERS = {"python", "python3", "node", "bash", "sh", "zsh", "ruby", "perl", "deno", "bun"}
FILLER = {"mach", "mache", "machen", "alles", "kannst", "kann", "wenn", "werden", "wird", "bitte", "noch", "auch", "jetzt",
          "dann", "eigene", "eigentlich", "einfach", "please", "can", "you", "now", "just", "also", "then", "make", "let"}
GIT_READ = {"status", "log", "diff", "show", "grep", "branch", "remote", "rev-parse", "fetch", "ls-files", "blame", "config"}
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_WORD = re.compile(r"^[a-z][a-z0-9:_-]{0,24}$")


def normalize(cmd: str) -> str:
    """`gh pr create --title "x" --body y` → `gh pr create --title --body`; read-only commands → ''."""
    try:
        toks = shlex.split(cmd, comments=True)
    except ValueError:
        toks = cmd.split()
    while toks and _ENV.match(toks[0]):
        toks = toks[1:]
    while toks and toks[0] in ("sudo", "time", "do", "then", "else", "{", "(", "!"):
        toks = toks[1:]
    if not toks:
        return ""
    head = Path(toks[0]).name
    if head in READ_ONLY or head in SHELL or (head == "git" and len(toks) > 1 and toks[1] in GIT_READ):
        return ""
    out = [head]
    for t in toks[1:]:
        if t.startswith("-"):
            out.append(t.split("=", 1)[0])
        elif out[-1] == "-m" and _WORD.match(t.replace(".", "_")):
            out.append(t)  # python -m <module>: the module is the command
        elif _WORD.match(t) and len(out) < 4 and not any(x.startswith("-") for x in out[1:]):
            out.append(t)
        if len(out) >= 6:
            break
    if head in INTERPRETERS and (len(out) == 1 or out[1] in ("-", "-c", "-e")):
        return ""  # an inline script is not a reusable step
    return " ".join(out)


def _steps(tr) -> list[tuple[str, str, str]]:
    """(normalized, raw command, prompt that started it) for every action command in a session."""
    out, prompt = [], ""
    for t in tr.turns:
        if t.role == "user":
            prompt = t.text
        if t.role != "tool_call" or t.tool not in ("Bash", "shell", "exec_command", "local_shell_call"):
            continue
        try:
            d = json.loads(t.text)
        except (json.JSONDecodeError, TypeError):
            continue
        c = d.get("command") or d.get("cmd") or ""
        if isinstance(c, list):
            c = " ".join(map(str, c[2:] if c[:2] in (["bash", "-lc"], ["sh", "-c"]) else c))
        for part in re.split(r"\s*(?:&&|;|\|\|)\s*", command_head(str(c))):
            part = part.split("|", 1)[0].strip()
            n = normalize(part)
            if n and (not out or out[-1][0] != n):
                raw = short(part, 160)
                out.append((n, n if secrets.scan(raw) else raw, prompt))  # a token in a command never reaches a note
    return out


def mine(v: Vault, files: list[Path] | None = None, days: int = 60) -> list[dict]:
    """Recurring command runs → skill drafts (merged into `.brain/skills.jsonl`, decisions kept)."""
    cutoff = time.time() - days * 86400
    paths = files if files is not None else [f for f in learn.transcript_files(v) if f.stat().st_mtime >= cutoff]
    seen: dict[tuple, set] = defaultdict(set)
    example: dict[tuple, list] = {}
    prompts: dict[tuple, list] = defaultdict(list)
    for f in paths:
        tr = parse(f)
        if tr is None or tr.internal:
            continue
        steps = _steps(tr)
        names = [s[0] for s in steps]
        for n in range(3, 7):
            for i in range(len(names) - n + 1):
                gram = tuple(names[i:i + n])
                distinct, tools = len(set(gram)), len({g.split()[0] for g in gram})
                if distinct < len(gram) or distinct < 2 or (tools < 2 and distinct < 3):
                    continue  # `git add; git commit` alone, or the same step twice, is not a procedure
                if tr.session not in seen[gram]:
                    seen[gram].add(tr.session)
                    example.setdefault(gram, [s[1] for s in steps[i:i + n]])
                    prompts[gram].append(short(steps[i][2], 300))
    keep = [g for g, s in seen.items() if len(s) >= MIN_SESSIONS]
    keep.sort(key=lambda g: (-len(g), -len(seen[g])))
    maximal: list[tuple] = []
    for g in keep:  # drop runs that are part of a longer run seen (almost) as often
        if any(_inside(g, m) and len(seen[m]) >= len(seen[g]) - 1 for m in maximal):
            continue
        maximal.append(g)
    ranked: list[tuple] = []
    for g in sorted(maximal, key=lambda g: -(len(seen[g]) * len(g))):
        k = _core(g)
        if k and not any(len(k & _core(r)) / len(k | _core(r)) >= 0.6 for r in ranked):  # one draft per procedure
            ranked.append(g)
    old = {d["sig"]: d for d in read_jsonl(_path(v))}
    out = []
    for g in ranked[:30]:
        sig = digest(*g)[:10]
        words = _words(prompts[g])
        name = "-".join(dict.fromkeys(" ".join(x.split()[:2]) for x in g if not x.split()[1:2] == ["add"]))
        d = old.pop(sig, None) or {"id": "s-" + sig[:8], "sig": sig, "status": "open", "t": now_iso()}
        d.update({"steps": list(g), "examples": example[g], "sessions": len(seen[g]), "prompts": prompts[g][:5],
                  "words": words, "name": d.get("name") or slugify(name)[:40].strip("-")})
        out.append(d)
    keep_old = [d for d in old.values() if d.get("status") != "open"]  # approved/rejected stay remembered
    write_jsonl(_path(v), keep_old + out)
    return [d for d in out if d.get("status") == "open"]


def _core(g: tuple) -> set:
    """What makes a run distinctive: its commands, without the add/commit every run ends with."""
    return {" ".join(x.split()[:2]) for x in g} - {"git add", "git commit"}


def _inside(small: tuple, big: tuple) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _words(prompts: list[str]) -> list[str]:
    c = Counter()
    for p in prompts:
        c.update(set(terms(p)))
    return [w for w, k in c.most_common(12) if k >= 2 and w not in FILLER and "_" not in w][:4]


def _path(v: Vault) -> Path:
    return v.path("skills.jsonl")


def drafts(v: Vault, status: str | None = "open") -> list[dict]:
    return [d for d in read_jsonl(_path(v)) if status is None or d.get("status") == status]


def _skill_md(name: str, description: str, steps: list[str], sessions: int) -> str:
    lines = "\n".join(f"{i}. `{s}`" for i, s in enumerate(steps, 1))
    return (f"---\nname: brain-{name}\ndescription: {description}\n---\n# {name}\n\n"
            f"Steps that worked in {f'{sessions} ' if sessions else ''}past sessions (learned by self-improving-brain). "
            "Adapt arguments to the task;\n"
            f"before an irreversible step run `brain check --command \"<command>\"` and ask the user if it names a guard.\n\n"
            f"{lines}\n\nThe source of truth is the vault note `skill-{name}` (`brain read skill-{name}`): edit it there.\n")


def install_claude(name: str, description: str, steps: list[str], sessions: int) -> Path:
    p = Path.home() / ".claude" / "skills" / f"brain-{name}" / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_skill_md(name, description, steps, sessions), encoding="utf-8")
    return p


def approve(v: Vault, sid: str, name: str = "", description: str = "", words: list[str] | None = None,
            claude: bool = True) -> dict:
    all_d = read_jsonl(_path(v))
    d = next((x for x in all_d if x.get("id") == sid), None)
    if d is None:
        raise KeyError(f"no skill draft {sid}")
    name = slugify(name or d.get("name") or sid)
    if not description:
        raise ValueError('a skill needs a description: --description "<when to use it, one sentence>"')
    words = words if words is not None else d.get("words") or []
    lib = Library(v.root)
    if lib.find(f"skill-{name}"):
        raise ValueError(f"skill-{name} already exists — pick another --name")
    steps = d.get("examples") or d["steps"]
    lid = learn.unique_lesson_id(lib, f"skill {name}", "")
    lesson = Lesson(id=lid, note=f"skill-{name}", rule=f"For this task follow the skill-{name} procedure: `brain read skill-{name}`.",
                    mechanism="skill", words=words, since=today())
    body = "\n".join(f"{i}. `{s}`" for i, s in enumerate(steps, 1))
    text = (f"---\nname: skill-{name}\ndescription: {description}\ntype: resource\nskill: {name}\nupdated: {today()}\n---\n"
            f"# Skill: {name}\n\n{description}\nLearned from {d.get('sessions', 0)} sessions; part of [[hub-brain]].\n\n"
            f"## Steps\n\n{body}\n\n## Started by prompts like\n\n"
            + "\n".join(f"- “{short(p, 140)}”" for p in d.get("prompts", [])[:3]) + "\n\n" + lesson.as_callout() + "\n")
    dest = v.root / "30-resources" / "skills" / f"skill-{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    trust.record(v, [lesson], "skill")
    d.update({"status": "approved", "name": name, "description": description, "note": f"skill-{name}", "lesson": lid})
    write_jsonl(_path(v), all_d)
    out = {"note": dest, "lesson": lid, "claude": None}
    if claude:
        out["claude"] = install_claude(name, description, steps, d.get("sessions", 0))
    return out


def reject(v: Vault, sid: str) -> bool:
    all_d = read_jsonl(_path(v))
    hit = False
    for d in all_d:
        if d.get("id") == sid:
            d["status"], hit = "rejected", True
    write_jsonl(_path(v), all_d)
    return hit


def installed(v: Vault) -> list[dict]:
    out = []
    for n in Library(v.root).notes.values():
        name = str(n.meta.get("skill") or "")
        if name:
            steps = [m.group(1) for m in re.finditer(r"^\d+\. `(.+)`$", n.body, re.M)]
            out.append({"name": name, "note": n.name, "description": n.description, "steps": steps})
    return out


def sync(v: Vault) -> list[Path]:
    """(Re)install every approved skill from the vault as a Claude Code skill."""
    return [install_claude(s["name"], s["description"], s["steps"], 0) for s in installed(v) if s["steps"]]
