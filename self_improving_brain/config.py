"""Where the vault lives and how it is tuned.

Resolution order: --vault flag > $BRAIN_VAULT > ~/.config/self-improving-brain/config.json > ~/brain.
A directory only counts as a vault if it carries `.brain/self-improving-brain.json` — so self-improving-brain
never touches an unrelated folder that happens to be called `brain`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .util import load_json, save_json

MARKER = "self-improving-brain.json"
VERSION = 1

DEFAULTS = {
    "version": VERSION,
    # recall: inject at most this many notes per prompt, only above this score
    "recall_max_notes": 2,
    "recall_threshold": 4.0,
    # graph recall: share of a seed's score passed to linked notes
    "graph_beta": 0.3,
    "graph_seeds": 3,
    # lessons with mechanism "always" are capped to keep session start cheap
    "always_max": 8,
    # optional local LLM command for autonomous learning/distilling. Prompt arrives on stdin,
    # answer is read from stdout. Examples:
    #   "claude -p --model haiku"     "codex exec -"     "ollama run llama3.1"
    # Empty = no LLM: your agent does the thinking the next time you review.
    "llm_cmd": "",
    "llm_timeout": 240,
    # git sync at the end of nightly runs (only if the vault is a git repo with a remote)
    "git_sync": True,
    # notes of type project that were not updated for this many days are flagged
    "stale_days": 45,
    # transcript sources to learn from (globs, ~ expanded)
    "transcripts": [
        "~/.claude/projects/**/*.jsonl",
        "~/.codex/sessions/**/*.jsonl",
    ],
}


class NotAVault(RuntimeError):
    pass


@dataclass
class Vault:
    root: Path
    cfg: dict = field(default_factory=dict)

    @property
    def meta(self) -> Path:
        return self.root / ".brain"

    def path(self, *parts: str) -> Path:
        return self.meta.joinpath(*parts)

    def get(self, key: str):
        return self.cfg.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.cfg[key] = value
        save_json(self.meta / MARKER, self.cfg)


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "self-improving-brain" / "config.json"


def candidate_roots(explicit: str | None = None) -> list[Path]:
    out = []
    if explicit:
        out.append(Path(explicit).expanduser())
    if os.environ.get("BRAIN_VAULT"):
        out.append(Path(os.environ["BRAIN_VAULT"]).expanduser())
    uc = load_json(user_config_path(), {})
    if uc.get("vault"):
        out.append(Path(uc["vault"]).expanduser())
    out.append(Path.home() / "brain")
    return out


def is_vault(p: Path) -> bool:
    return (p / ".brain" / MARKER).is_file()


def open_vault(explicit: str | None = None) -> Vault:
    for root in candidate_roots(explicit):
        if is_vault(root):
            cfg = dict(DEFAULTS)
            cfg.update(load_json(root / ".brain" / MARKER, {}))
            return Vault(root.resolve(), cfg)
        if explicit:
            break
    raise NotAVault(
        "No self-improving-brain vault found. Create one with:  brain init ~/brain\n"
        "(or point to it with --vault PATH or $BRAIN_VAULT)"
    )


def remember_vault(root: Path) -> None:
    p = user_config_path()
    data = load_json(p, {})
    data["vault"] = str(root)
    save_json(p, data)
