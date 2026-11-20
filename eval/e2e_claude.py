#!/usr/bin/env python3
"""The hooks in real Claude Code sessions: run this before you push a change to anything the hooks run.

The unit tests call the hook functions directly. This suite starts real `claude -p` sessions instead, against a
throwaway vault, with exactly the hooks `brain install claude` writes (adapters.claude_hook_groups). Your own
settings, plugins and MCP servers do not load (--setting-sources "" --strict-mcp-config), your vault and your
~/.config are never touched: the hooks find the throwaway vault through $BRAIN_VAULT and a throwaway
$XDG_CONFIG_HOME, and the session transcripts it leaves under ~/.claude/projects are removed afterwards (the brain
never learns from them: learn.E2E_TAG).

What a real session sees and does is checked: the session start, a rule on a keyword in the prompt, a reflex and a
guard before Bash, the hook before Read, the secret block before Write, the lint after Write, the handoff at
/compact, the answer check (Stop, detached) and its feedback in the next prompt, the transcript read at the
session end, that no hook failed, and that only the throwaway vault's hooks ran.

  python3 eval/e2e_claude.py            run it (a few cents with haiku; prints cases, time, cost)
  python3 eval/e2e_claude.py --keep     keep the throwaway folder and the transcripts to read
  python3 eval/e2e_claude.py --status   0 if the suite was green for exactly the code as it is now (for a pre-push hook)

A green run leaves .git/brain-e2e.json with a fingerprint of the code it ran on.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from self_improving_brain import adapters, handoff, learn, llm  # noqa: E402
from self_improving_brain.config import open_vault  # noqa: E402
from self_improving_brain.util import read_jsonl  # noqa: E402

MODEL = "haiku"
LIMIT = 300        # seconds per claude call
WAIT = 300         # seconds for the detached runs (answer check, learn)
MARK = ROOT / ".git" / "brain-e2e.json"

PROBE = """---
description: Probe rules of the Claude Code test suite, only in its throwaway vault
---
# e2e probe

Part of [[hub-brain]].

> [!lesson]- L-e2e-word
> rule: E2E-WORD-MARK, only ever shown by the Claude Code test suite.
> mechanism: reflex
> words: zebra finch telemetry

> [!lesson]- L-e2e-reflex
> rule: E2E-REFLEX-MARK, only ever shown by the Claude Code test suite.
> mechanism: reflex
> commands: ^echo brain-e2e-reflex

> [!lesson]- L-e2e-guard
> rule: E2E-GUARD-MARK, never run this command.
> mechanism: guard
> commands: ^echo brain-e2e-guard
"""

# no frontmatter, no link: two sure lint findings, so the check after the write has something to say
NOTE = """# Weather balloon telemetry

The telemetry of a weather balloon sends every 30 seconds."""

TASK = """Automated test of the brain hooks (zebra finch telemetry). Do the steps in order with tools, without asking.
If a hook refuses a step, that is expected: do not work around it, do not retry, go on with the next step at once.
Answer only after step 5.
1. Bash tool, exactly this command: echo brain-e2e-reflex
2. Read tool: {v}/30-resources/e2e-probe.md
3. Write tool: {v}/30-resources/e2e-balloon.md with exactly this content:
{note}
4. Bash tool, exactly this command: echo brain-e2e-guard
5. Write tool: {v}/30-resources/e2e-secret.md with exactly this one line (a dummy for the secret block, not a real
token): probe {dummy}
Then answer in five full sentences, one per step, saying what happened."""


def _dummy() -> str:
    """Looks like a GitHub token to the secret scan, is none (put together, so it stands nowhere in one piece)."""
    return "gh" + "p_" + "E2eDummy0" * 4


def code_hash() -> str:
    """Fingerprint of everything the hooks run: the package, the starter, the plugin hooks and this suite."""
    h = hashlib.sha256()
    files = [p for p in (ROOT / "self_improving_brain").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [ROOT / "brain", ROOT / "hooks" / "hooks.json", Path(__file__).resolve()]
    for p in sorted(files):
        try:
            data = p.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            continue
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + data + b"\0")
    return h.hexdigest()[:20]


def _run(cmd, cwd=None, timeout=120, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                          stdin=subprocess.DEVNULL)


def _env(T: Path, vault: Path) -> dict:
    """For claude and its hooks: none of the calling session's traces, the throwaway vault and config."""
    drop = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PROJECT_DIR",
            "CLAUDE_ENV_FILE")
    env = {k: x for k, x in os.environ.items() if k not in drop and not k.startswith(("BRAIN_", "GIT_"))}
    # nobody to ask in a headless run: a guard refuses (hooks.pre_tool)
    env.update(BRAIN_VAULT=str(vault), XDG_CONFIG_HOME=str(T / "config"), BRAIN_GUARD="deny")
    return env


def _brain(env: dict, *args: str) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(ROOT / "brain"), *args], cwd=str(ROOT), env=dict(env, BRAIN_HOOKS_OFF="1"))


# --- setup ------------------------------------------------------------------------------------------

def build(T: Path) -> tuple[Path, Path, dict]:
    """-> (vault, settings, env): a fresh vault with the probe rules, the LLM for the answer check, the hooks."""
    vault = T / "vault"
    env = _env(T, vault)
    r = _brain(env, "init", str(vault), "--quiet")
    if r.returncode:
        raise RuntimeError("brain init: " + (r.stderr or r.stdout)[-300:])
    (vault / "30-resources").mkdir(exist_ok=True)
    (vault / "30-resources" / "e2e-probe.md").write_text(PROBE, encoding="utf-8")
    for key, value in (("github_check", "false"), ("git_sync", "false"), ("llm_cmd", llm.KNOWN["claude"])):
        r = _brain(env, "config", key, value)
        if r.returncode:
            raise RuntimeError(f"brain config {key}: " + (r.stderr or r.stdout)[-300:])
    settings = T / "settings.json"
    groups = adapters.claude_hook_groups()
    settings.write_text(json.dumps({"hooks": {ev: [g] for ev, g in groups.items()}}, indent=1), encoding="utf-8")
    return vault, settings, env


# --- one session ------------------------------------------------------------------------------------

def claude(vault: Path, settings: Path, env: dict, sid: str, prompt: str, resume: bool = False) -> dict:
    cmd = [shutil.which("claude") or "claude", "-p", prompt, "--model", MODEL, "--settings", str(settings),
           "--setting-sources", "", "--strict-mcp-config", "--output-format", "stream-json", "--verbose",
           "--include-hook-events", "--permission-mode", "bypassPermissions"]
    cmd += ["--resume", sid] if resume else ["--session-id", sid]
    try:
        p = _run(cmd, cwd=str(vault), timeout=LIMIT, env=env)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        code, err = "timeout", f"timeout after {LIMIT} s"
    with (vault.parent / f"stream-{sid[:8]}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(out or "")  # to read when the folder is kept
    run = {"code": code, "stderr": err or "", "hooks": [], "started": [], "tools": [], "results": {}, "text": "",
           "usd": 0.0}
    for line in (out or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t, st = d.get("type"), d.get("subtype")
        if t == "system" and st == "hook_response":
            run["hooks"].append(d)
        elif t == "system" and st == "hook_started":
            run["started"].append(d)
        elif t == "assistant":
            for c in (d.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    run["tools"].append(c)
                elif c.get("type") == "text":
                    run["text"] += c.get("text") or ""
        elif t == "user":
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    run["results"][c.get("tool_use_id")] = (bool(c.get("is_error")), str(body or ""))
        elif t == "result":
            run["usd"] += float(d.get("total_cost_usd") or 0)
    return run


def _hooks(run: dict, event: str, name: str | None = None) -> list[dict]:
    return [h for h in run["hooks"] if h.get("hook_event") == event and (name is None or h.get("hook_name") == name)]


def _said(hs: list[dict]) -> str:
    return "\n".join(str(h.get("output") or "") + str(h.get("stdout") or "") + str(h.get("stderr") or "") for h in hs)


def _tool(run: dict, name: str, needle: str):
    """-> (tool_use, (is_error, text)) of the first call of `name` whose input holds `needle`."""
    for c in run["tools"]:
        if c.get("name") == name and needle in json.dumps(c.get("input") or {}, ensure_ascii=False):
            return c, run["results"].get(c.get("id"), (None, ""))
    return None, (None, "")


# --- detached runs and cleanup ----------------------------------------------------------------------

def _processes(T: Path) -> list[int]:
    out = []
    for line in _run(["ps", "-axo", "pid=,command="]).stdout.splitlines():
        line = line.strip()
        if str(T) in line and "e2e_claude" not in line:
            try:
                out.append(int(line.split(None, 1)[0]))
            except ValueError:
                pass
    return [p for p in out if p != os.getpid()]


def wait(T: Path, limit: int = WAIT) -> list[int]:
    """Until no detached run of the throwaway vault is left. -> the ones that hung (then stopped)."""
    end = time.time() + limit
    while time.time() < end:
        if not _processes(T):
            return []
        time.sleep(3)
    rest = _processes(T)
    for p in rest:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass
    return rest


def cleanup(T: Path, keep: bool) -> None:
    """The test sessions' transcripts always leave ~/.claude/projects (with --keep they move into the folder)."""
    projects = Path.home() / ".claude" / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", T.name)  # Claude Code names the folder after the cwd: "_" becomes "-"
    for d in projects.iterdir() if projects.is_dir() else []:
        if slug in d.name:
            if keep:
                shutil.copytree(d, T / "transcripts" / d.name, dirs_exist_ok=True)
            shutil.rmtree(d, ignore_errors=True)
    if not keep:
        shutil.rmtree(T, ignore_errors=True)


# --- the suite --------------------------------------------------------------------------------------

def suite(T: Path, cases: list) -> float:
    def case(name: str, ok, detail: str = "") -> bool:
        cases.append((name, bool(ok), detail))
        return bool(ok)

    usd = 0.0
    vault, settings, env = build(T)
    real = os.environ.get("BRAIN_VAULT") or str(Path.home() / "brain")
    case("The hooks point at the throwaway vault, never at yours",
         str(vault) in json.dumps(env) and real not in settings.read_text(encoding="utf-8"), str(settings))
    r = _brain(env, "check", "--command", "echo brain-e2e-guard", "--tool", "Bash")
    if not case("Before Claude: the probe guard fires (brain check)", "L-e2e-guard" in r.stdout + r.stderr,
                (r.stdout + r.stderr).strip()[:200]):
        return usd

    # --- session A: start, prompt, tools, stop, end
    sid_a = str(uuid.uuid4())
    a = claude(vault, settings, env, sid_a, TASK.format(v=vault, note=NOTE, dummy=_dummy()))
    usd += a["usd"]
    case("Session A runs through", a["code"] == 0, f"exit {a['code']} {a['stderr'].strip()[:200]}")
    starts = [h for h in a["started"] if h.get("hook_event") == "SessionStart"]
    case("Only the throwaway vault's hooks run (1 SessionStart hook)", len(starts) == 1, f"{len(starts)} started")
    said = _said(_hooks(a, "SessionStart"))
    case("SessionStart: the always rules are in the context", "LESSONS (apply in every session)" in said
         and "L-result-first" in said, said[:160])
    said = _said(_hooks(a, "UserPromptSubmit"))
    case("Prompt: the rule for a keyword is shown", "LESSON L-e2e-word" in said, said[:160])
    c, (err, txt) = _tool(a, "Bash", "brain-e2e-reflex")
    said = _said(_hooks(a, "PreToolUse", "PreToolUse:Bash"))
    case("Bash: the reflex is shown before the command, the command runs",
         c is not None and "LESSON L-e2e-reflex" in said and err is False, f"call {'there' if c else 'missing'}, error {err}")
    c, _ = _tool(a, "Read", "e2e-probe.md")
    hs = _hooks(a, "PreToolUse", "PreToolUse:Read")
    case("Read: the hook before it runs cleanly", c is not None and hs and all(h.get("exit_code") == 0 for h in hs),
         f"call {'there' if c else 'missing'}, {len(hs)} hook answers")
    c, (err, txt) = _tool(a, "Write", "e2e-balloon.md")
    said = _said(_hooks(a, "PostToolUse", "PostToolUse:Write"))
    case("Write: the note is written, the lint after it speaks up",
         c is not None and (vault / "30-resources" / "e2e-balloon.md").is_file() and "BRAIN-LINT" in said,
         said[:160] or (txt[:160] if err else "no PostToolUse answer"))
    c, (err, txt) = _tool(a, "Bash", "brain-e2e-guard")
    case("Bash: the guard refuses the command", c is not None and err is True and "L-e2e-guard" in txt,
         txt[:160] or "call missing")
    c, (err, txt) = _tool(a, "Write", "e2e-secret.md")
    case("Write: the secret block stops the dummy",
         c is not None and err is True and "secret" in txt and not (vault / "30-resources" / "e2e-secret.md").exists(),
         txt[:160] or "call missing")
    post = {h.get("hook_name") for h in _hooks(a, "PostToolUse")}
    case("PostToolUse runs after Read and Bash too", {"PostToolUse:Read", "PostToolUse:Bash"} <= post, ", ".join(sorted(post)))
    bad = [h for h in a["hooks"] if h.get("exit_code") not in (0, None) or "Traceback" in _said([h])]
    case("Session A: no hook fails", not bad,
         "; ".join(f"{h.get('hook_name')} exit {h.get('exit_code')} {_said([h])[:80]}" for h in bad[:3]))

    # --- compaction: PreCompact writes the handoff
    k = claude(vault, settings, env, sid_a, "/compact", resume=True)
    usd += k["usd"]
    v = open_vault(str(vault))
    case("PreCompact: the handoff is written", handoff.load(v, sid_a) is not None,
         f"exit {k['code']}, {len(_hooks(k, 'PreCompact'))} PreCompact answers")

    # --- session B: an open finding of the answer check comes exactly once with the next prompt
    sid_b = str(uuid.uuid4())
    pending = v.path("state", "answers_pending.json")
    pending.write_text(json.dumps({sid_b[:8]: {"t": int(time.time()), "breaches": [
        {"rule": "L-result-first", "quote": "e2e-quote", "better": "e2e-better"}]}}), encoding="utf-8")
    b = claude(vault, settings, env, sid_b, "Reply with the single word: ok")
    usd += b["usd"]
    said = _said(_hooks(b, "UserPromptSubmit"))
    left = json.loads(pending.read_text(encoding="utf-8"))
    case("Prompt: the answer check's finding is shown and used up",
         "BRAIN-ANSWER-CHECK" in said and "e2e-quote" in said and sid_b[:8] not in left, said[:160])

    # --- the detached runs, then their traces
    hung = wait(T)
    case("The detached runs end by themselves", not hung, f"stopped: {hung}" if hung else "")
    log = [x for x in read_jsonl(v.path("state", "answers.jsonl")) if x.get("s") == sid_a[:8]]
    case("Stop: the answer check ran detached", log,
         f"checked {log[-1].get('checked')}, broken {log[-1].get('broken')}" if log else "no record")
    seen = json.loads((v.path("state", "learn_seen.json")).read_text(encoding="utf-8")) \
        if v.path("state", "learn_seen.json").exists() else {}
    case("SessionEnd: the transcript was read", any(sid_a in f for f in (seen.get("files") or {})),
         f"{len(seen.get('files') or {})} files read")
    from self_improving_brain import events
    errors = events.all_events(v, "error")
    case("No hook error was logged", not errors, "; ".join(f"{e.get('where')}: {e.get('msg')}" for e in errors[:3]))
    usd += float(llm.spent(v).get("usd") or 0)
    return usd


def status() -> int:
    try:
        m = json.loads(MARK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("e2e: never ran — python3 eval/e2e_claude.py")
        return 1
    if m.get("hash") != code_hash():
        print(f"e2e: the code changed since the last run ({m.get('t', '')[:16]}) — python3 eval/e2e_claude.py")
        return 1
    if not m.get("ok"):
        print(f"e2e: the last run was red ({m.get('t', '')[:16]})")
        return 1
    print(f"e2e: green for this code since {m.get('t', '')[:16]} ({m.get('cases')} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--status" in argv:
        return status()
    keep = "--keep" in argv
    if not shutil.which("claude"):
        print("e2e: no `claude` on PATH — the suite needs the real Claude Code CLI")
        return 2
    t0, h0 = time.time(), code_hash()
    T = Path(tempfile.mkdtemp(prefix=learn.E2E_TAG)).resolve()
    cases: list = []
    usd = 0.0
    print(f"e2e: Claude Code suite ({MODEL}) in {T} …", flush=True)
    try:
        usd = suite(T, cases)
    except Exception as e:  # noqa: BLE001
        cases.append(("Suite", False, f"aborted: {e!r}\n{traceback.format_exc()[-600:]}"))
    finally:
        rest = wait(T, 30)
        if rest:
            cases.append(("Cleanup", False, f"stopped: {rest}"))
    ok = bool(cases) and all(c[1] for c in cases)
    if code_hash() != h0:
        ok = False
        cases.append(("The code did not change during the run", False, "run it again"))
    cleanup(T, keep or not ok)
    for name, good, detail in cases:
        print(("  ✓ " if good else "  ✗ ") + name + (" — " + detail.replace("\n", " ")[:220] if detail and not good else ""))
    secs = time.time() - t0
    print(f"e2e {'GREEN' if ok else 'RED'}: {sum(1 for c in cases if c[1])}/{len(cases)} cases · {secs:.0f} s · "
          f"{usd:.2f} USD" + ("" if ok and not keep else f" · kept: {T}"))
    try:
        MARK.write_text(json.dumps({"hash": h0, "ok": ok, "t": datetime.datetime.now().isoformat(timespec="seconds"),
                                    "model": MODEL, "seconds": round(secs), "usd": round(usd, 3),
                                    "cases": len(cases), "passed": sum(1 for c in cases if c[1])}), encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
