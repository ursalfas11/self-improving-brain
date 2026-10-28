"""Optional local LLM call. No API keys inside self-improving-brain: we shell out to whatever CLI you use.

Configure once:  brain config llm_cmd "claude -p --model haiku"
             or  brain config llm_cmd "codex exec -"
             or  brain config llm_cmd "ollama run llama3.1"
The prompt goes to stdin, the answer comes from stdout. BRAIN_HOOKS_OFF=1 is set so the
child agent does not trigger the brain's own hooks.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess

from .config import Vault


# Every prompt we send starts with this, so `brain learn` can recognise and skip transcripts of
# our own headless calls (some CLIs keep a session log even in print mode).
MARKER = "[self-improving-brain:internal]"

# Headless invocations known to work: no tools, no user settings/hooks, no session log.
KNOWN = {
    "claude": 'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence',
    "codex": "codex exec --skip-git-repo-check --ephemeral --sandbox read-only",
    "ollama": "ollama run llama3.1",
}


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


def ask(v: Vault, prompt: str) -> str:
    cmd = str(v.get("llm_cmd") or "").strip()
    if not cmd:
        raise NoLLM("no llm_cmd configured")
    env = dict(os.environ, BRAIN_HOOKS_OFF="1")
    try:
        res = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True,
                             timeout=int(v.get("llm_timeout") or 240), env=env)
    except subprocess.TimeoutExpired:
        raise NoLLM(f"llm_cmd timed out after {v.get('llm_timeout')} s") from None
    except (OSError, ValueError) as e:  # CLI missing on this machine, or a broken llm_cmd
        raise NoLLM(f"llm_cmd could not run: {e}") from None
    if res.returncode != 0:
        raise NoLLM(f"llm_cmd failed ({res.returncode}): {res.stderr.strip()[:300]}")
    return res.stdout


def ask_json(v: Vault, prompt: str):
    out = ask(v, prompt + "\n\nAnswer with JSON only, no prose, no code fences.")
    m = re.search(r"(\[.*\]|\{.*\})", out, re.S)
    if not m:
        raise NoLLM("llm answer contained no JSON")
    return json.loads(m.group(1))
