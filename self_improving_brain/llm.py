"""The LLM behind the nightly learning. No API keys inside self-improving-brain: we shell out to a CLI you
already use and are logged in to.

`brain setup` picks one (claude → codex → ollama). Change it with  brain config llm_cmd "<command>".
The prompt goes to stdin, the answer comes from stdout. BRAIN_HOOKS_OFF=1 keeps the brain's own hooks out
of the child process.

Every call counts against a daily budget (`llm_budget_usd`, `llm_max_calls`), kept per machine in
`.brain/state/llm.json`. Claude Code reports what a call cost; for other CLIs only the calls are counted.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess

from .config import Vault
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
    for old in sorted(d)[:-90]:  # three months are enough for every number the brain shows
        del d[old]
    save_json(_ledger_path(v), d)


def budget_left(v: Vault) -> bool:
    s = spent(v)
    return s["calls"] < int(v.get("llm_max_calls")) and s["usd"] < float(v.get("llm_budget_usd"))


def ask(v: Vault, prompt: str) -> str:
    cmd = str(v.get("llm_cmd") or "").strip()
    if not cmd:
        raise NoLLM("no llm_cmd configured")
    if not budget_left(v):
        raise NoLLM("today's learning budget is used up (llm_budget_usd / llm_max_calls)")
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
            if d.get("is_error"):
                _book(v, usd, tokens)
                raise NoLLM(f"llm_cmd failed: {str(d.get('result'))[:300]}")
            out = str(d.get("result") or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    _book(v, usd, tokens)
    if res.returncode != 0:
        raise NoLLM(f"llm_cmd failed ({res.returncode}): {res.stderr.strip()[:300]}")
    return out


def ask_json(v: Vault, prompt: str):
    out = ask(v, prompt + "\n\nAnswer with JSON only, no prose, no code fences.")
    m = re.search(r"(\[.*\]|\{.*\})", out, re.S)
    if not m:  # a bad answer is about this prompt, not the LLM: the caller moves on to the next item
        raise ValueError("llm answer contained no JSON")
    return json.loads(m.group(1))
