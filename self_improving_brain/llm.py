"""The LLM behind the nightly learning. No API keys inside self-improving-brain: we shell out to a CLI you
already use and are logged in to.

`brain setup` picks one (claude → codex → ollama). Change it with  brain config llm_cmd "<command>".
The prompt goes to stdin, the answer comes from stdout. BRAIN_HOOKS_OFF=1 keeps the brain's own hooks out
of the child process.

Every call counts against a daily budget (`llm_budget_usd`, `llm_max_calls`), kept per machine in
`.brain/state/llm.json`. Claude Code reports what a call cost; for other CLIs only the calls are counted.
One purpose may use only its SHARE of the day, so filing facts can not starve the rest of the night.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess

from .config import Vault
from .secrets import redact
from .util import load_json, save_json, today

# Every prompt we send starts with this, so `brain learn` can recognise and skip transcripts of
# our own headless calls (some CLIs keep a session log even in print mode).
MARKER = "[self-improving-brain:internal]"

# Headless invocations known to work: no tools, no user settings, hooks, MCP servers or skills, no session log.
KNOWN = {
    "claude": 'claude -p --model haiku --tools "" --setting-sources "" --strict-mcp-config --disable-slash-commands '
              '--no-session-persistence --output-format json',
    "codex": "codex exec --skip-git-repo-check --ephemeral --sandbox read-only",
    "ollama": "ollama run llama3.1",
}


SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SSE_PORT")


def detect() -> str:
    """The best LLM CLI on this machine, or ''."""
    import shutil
    for name in ("claude", "codex", "ollama"):
        if shutil.which(name):
            return KNOWN[name]
    return ""


class NoLLM(RuntimeError):
    pass


def available(v: Vault) -> bool:
    return bool(str(v.get("llm_cmd") or "").strip())


def _ledger_path(v: Vault):
    return v.path("state", "llm.json")


def spent(v: Vault, day: str | None = None) -> dict:
    """{calls, usd, tokens} spent on `day` (default today) on this machine."""
    d = load_json(_ledger_path(v), {})
    row = d.get(day or today()) if isinstance(d, dict) else None
    return {"calls": 0, "usd": 0.0, "tokens": 0, **(row if isinstance(row, dict) else {})}


def spent_since(v: Vault, first_day: str) -> dict:
    d = load_json(_ledger_path(v), {})
    out = {"calls": 0, "usd": 0.0, "tokens": 0}
    for day, row in (d.items() if isinstance(d, dict) else []):
        if day >= first_day and isinstance(row, dict):
            for k in out:
                out[k] += row.get(k, 0) or 0
    out["usd"] = round(out["usd"], 4)
    return out


def _book(v: Vault, usd: float | None, tokens: int) -> None:
    d = load_json(_ledger_path(v), {})
    d = d if isinstance(d, dict) else {}
    row = d.setdefault(today(), {"calls": 0, "usd": 0.0, "tokens": 0})
    row["calls"] += 1
    row["usd"] = round(row["usd"] + (usd or 0.0), 6)
    row["tokens"] += tokens
    if _PURPOSE:
        by = row.setdefault("by", {}).setdefault(_PURPOSE, {"calls": 0, "usd": 0.0})
        by["calls"] += 1
        by["usd"] = round(by["usd"] + (usd or 0.0), 6)
    for old in sorted(d)[:-90]:  # three months are enough for every number the brain shows
        del d[old]
    save_json(_ledger_path(v), d)


# At most this share of the day's budget per purpose. Filing facts once used up the whole day on its own, and
# the upkeep after it got no call. The answer check runs after every answer, so it gets a small share.
SHARE = {"distill": 0.5, "answers": 0.25}
_PURPOSE = ""


@contextlib.contextmanager
def purpose(name: str):
    """Book the calls inside to `name` and hold them to its SHARE: `with llm.purpose("distill"): ...`"""
    global _PURPOSE
    before, _PURPOSE = _PURPOSE, name
    try:
        yield
    finally:
        _PURPOSE = before


def budget_left(v: Vault, what: str | None = None) -> bool:
    s = spent(v)
    max_calls, max_usd = int(v.get("llm_max_calls")), float(v.get("llm_budget_usd"))
    if not (s["calls"] < max_calls and s["usd"] < max_usd):
        return False
    what = _PURPOSE if what is None else what
    share = SHARE.get(what)
    if not share:
        return True
    by = (s.get("by") or {}).get(what) or {}
    return by.get("calls", 0) < max(1, int(share * max_calls)) and by.get("usd", 0.0) < share * max_usd


PROBLEMS: list[str] = []  # why the LLM stopped in this process: the nightly report shows it, not only the log


def ask(v: Vault, prompt: str) -> str:
    try:
        return _ask(v, prompt)
    except NoLLM as e:
        PROBLEMS.append(str(e))
        raise


def _ask(v: Vault, prompt: str) -> str:
    cmd = str(v.get("llm_cmd") or "").strip()
    if not cmd:
        raise NoLLM("no llm_cmd configured")
    if not budget_left(v):
        if _PURPOSE in SHARE and budget_left(v, ""):
            raise NoLLM(f"today's learning budget for {_PURPOSE} is used up (at most {SHARE[_PURPOSE]:.0%} of the day)")
        raise NoLLM("today's learning budget is used up (llm_budget_usd / llm_max_calls)")
    prompt = redact(prompt)  # notes and drafts are scanned already; this is the last gate before text leaves
    # a run started from a session hook must not look like part of that session to the CLI
    env = {k: val for k, val in os.environ.items() if k not in SESSION_VARS and not k.startswith("CLAUDE_CODE_MESSAGING")}
    env["BRAIN_HOOKS_OFF"] = "1"
    try:
        res = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True,
                             timeout=int(v.get("llm_timeout") or 240), env=env)
    except subprocess.TimeoutExpired:
        _book(v, None, len(prompt) // 4)
        raise NoLLM(f"llm_cmd timed out after {v.get('llm_timeout')} s") from None
    except (OSError, ValueError) as e:  # CLI missing on this machine, or a broken llm_cmd
        raise NoLLM(f"llm_cmd could not run: {e}") from None
    out, usd, tokens = res.stdout, None, (len(prompt) + len(res.stdout)) // 4
    try:  # `claude -p --output-format json`: the answer plus what it cost
        d = json.loads(out)
        if isinstance(d, dict) and d.get("type") == "result":
            u = d.get("usage") or {}
            usd = float(d.get("total_cost_usd") or 0.0)
            tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens")) or tokens
            if d.get("is_error"):  # which error it was, not only that one happened
                _book(v, usd, tokens)
                raise NoLLM(f"llm_cmd failed ({d.get('subtype') or 'error'}): {str(d.get('result'))[:300]}")
            out = str(d.get("result") or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    _book(v, usd, tokens)
    if res.returncode != 0:
        raise NoLLM(f"llm_cmd failed ({res.returncode}): {res.stderr.strip()[:300]}")
    return out


def reachable(v: Vault) -> tuple[bool, str]:
    """One tiny call before the steps that think. A machine asleep or offline fails every call within a second;
    without this the night would count as done and the steps that need the LLM would be lost for the day.
    -> (ok, why). An exhausted budget is not "unreachable": the steps themselves stop at the budget."""
    if not budget_left(v, ""):
        return True, "budget"
    try:
        with purpose("probe"):
            ask_json(v, MARKER + '\nReply with exactly this JSON: {"ok": true}')
    except NoLLM as e:  # in PROBLEMS now: the nightly report names the failed call like any other
        return False, re.sub(r"\s+", " ", str(e)).strip()[:160]
    except (ValueError, OSError):
        pass  # it answered, only not as asked: reachable
    return True, ""


def ask_json(v: Vault, prompt: str):
    out = ask(v, prompt + "\n\nAnswer with JSON only, no prose, no code fences.")
    m = re.search(r"(\[.*\]|\{.*\})", out, re.S)
    if not m:  # a bad answer is about this prompt, not the LLM: the caller moves on to the next item
        raise ValueError("llm answer contained no JSON")
    return json.loads(m.group(1))
