"""Read agent session transcripts into one neutral shape.

Supported out of the box: Claude Code (~/.claude/projects/**/*.jsonl) and Codex CLI
(~/.codex/sessions/**/*.jsonl). Other agents: write a parser that yields the same Turn shape
(PRs welcome), or export your chats as JSONL lines {"role": "user"|"assistant", "text": ...}.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Our Stop-hook message carries this; agents may log it as a user turn, which must not count as one.
HOOK_MARK = "[self-improving-brain:check]"

REJECT_MARKERS = (
    "doesn't want to proceed", "does not want to proceed", "user rejected", "tool use was rejected",
    "user denied", "permission denied by user", "the user declined", "rejected by user",
)

# what an IDE or the agent puts in front of what you typed: it is not your words, and your words follow it
WRAPPED = re.compile(r"<(ide_[a-z_]+|system-reminder)>.*?</\1>", re.S)


def _rejection(txt: str, command_output: bool = False) -> bool:
    """The user refused the tool call: the CLI says so at the very start. A command's own output that mentions
    "user denied" somewhere (a log, a test fixture) is not a refusal: short, and no exit code."""
    head = txt.strip().lower()
    if command_output and (len(head) > 600 or '"exit_code"' in head):
        return False
    return any(m in head[:60] for m in REJECT_MARKERS)


@dataclass
class Turn:
    role: str            # user | assistant | tool_call | tool_result
    text: str = ""
    tool: str = ""
    rejected: bool = False
    ts: str = ""


@dataclass
class Transcript:
    path: Path
    session: str
    source: str
    turns: list[Turn]
    cwd: str = ""
    tokens: int = 0      # new tokens the session cost: input + cache writes + output (cache reads excluded)

    @property
    def internal(self) -> bool:
        """A transcript of one of our own headless LLM calls."""
        return any("[self-improving-brain:internal]" in t.text[:200] for t in self.turns[:3] if t.role == "user")

    @property
    def user_messages(self) -> list[Turn]:
        return [t for t in self.turns if t.role == "user"]


def _is_noise(text: str) -> bool:
    s = text.lstrip()
    if s.startswith("[self-improving-brain:internal]"):
        return False
    if HOOK_MARK in s[:600] or s.startswith("Stop hook feedback"):
        return True
    if s.startswith("# AGENTS.md instructions") or "<INSTRUCTIONS>" in s[:400]:
        return True  # Codex puts your AGENTS.md in front of the session as a user message: not something you typed
    return (not s or s.startswith("<") or s.startswith("Caveat:") or s.startswith("[Request interrupted")
            or s.startswith("This session is being continued"))


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") in ("text", "input_text", "output_text"):
                    parts.append(c.get("text", ""))
                elif c.get("type") == "tool_result":
                    parts.append(_text_of(c.get("content", "")))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(p for p in parts if p)
    return ""


def _claude(lines: list[dict], path: Path) -> Transcript:
    turns: list[Turn] = []
    session = cwd = ""
    usage: dict[str, int] = {}  # per message id: one answer is logged as several lines with the same usage
    for d in lines:
        session = session or d.get("sessionId", "")
        cwd = cwd or str(d.get("cwd") or "")
        t, msg, ts = d.get("type"), d.get("message"), str(d.get("timestamp") or "")
        if not isinstance(msg, dict) or d.get("isMeta"):
            continue
        u = msg.get("usage")
        if t == "assistant" and isinstance(u, dict):
            usage[str(msg.get("id") or len(usage))] = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "output_tokens"))
        if d.get("isSidechain"):
            continue
        content = msg.get("content")
        if t == "user":
            if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        txt = _text_of(c.get("content", ""))
                        rej = bool(c.get("is_error")) and _rejection(txt)
                        turns.append(Turn("tool_result", txt[:2000], rejected=rej, ts=ts))
                continue
            txt = WRAPPED.sub("", _text_of(content)).strip()
            if not _is_noise(txt):
                turns.append(Turn("user", txt, ts=ts))
        elif t == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text"):
                    turns.append(Turn("assistant", c["text"], ts=ts))
                elif c.get("type") == "tool_use":
                    turns.append(Turn("tool_call", json.dumps(c.get("input", {}), ensure_ascii=False)[:2000],
                                      tool=c.get("name", ""), ts=ts))
    return Transcript(path, session or path.stem, "claude", turns, cwd, sum(usage.values()))


def _codex(lines: list[dict], path: Path) -> Transcript:
    turns: list[Turn] = []
    session = cwd = ""
    tokens = 0
    for d in lines:
        p, ts = d.get("payload") or {}, str(d.get("timestamp") or "")
        if d.get("type") == "session_meta":
            session = p.get("id") or p.get("session_id") or session
            cwd = cwd or str(p.get("cwd") or "")
            continue
        if d.get("type") == "event_msg" and isinstance(p, dict) and p.get("type") == "token_count":
            u = (p.get("info") or {}).get("total_token_usage") or {}
            if isinstance(u, dict):  # running totals: the last one counts
                tokens = (int(u.get("input_tokens") or 0) - int(u.get("cached_input_tokens") or 0)
                          + int(u.get("output_tokens") or 0))
            continue
        if d.get("type") != "response_item" or not isinstance(p, dict):
            continue
        pt = p.get("type")
        if pt == "message":
            txt = _text_of(p.get("content"))
            if p.get("role") == "user" and not _is_noise(txt):
                turns.append(Turn("user", txt, ts=ts))
            elif p.get("role") == "assistant" and txt:
                turns.append(Turn("assistant", txt, ts=ts))
        elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
            arg = p.get("arguments") or p.get("input") or p.get("action") or ""
            turns.append(Turn("tool_call", str(arg)[:2000], tool=p.get("name", pt), ts=ts))
        elif pt in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            txt = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            rej = _rejection(txt, command_output=True)
            turns.append(Turn("tool_result", txt[:2000], rejected=rej, ts=ts))
    return Transcript(path, session or path.stem, "codex", turns, cwd, max(tokens, 0))


def _generic(lines: list[dict], path: Path) -> Transcript:
    turns = [Turn(d["role"], d.get("text", ""), ts=d.get("ts", "")) for d in lines
             if d.get("role") in ("user", "assistant") and isinstance(d.get("text"), str)]
    return Transcript(path, path.stem, "generic", turns)


def parse(path: Path) -> Transcript | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return parse_lines(fh, path)
    except OSError:
        return None


def parse_lines(raw_lines, path: Path) -> Transcript | None:
    lines = []
    for line in raw_lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            lines.append(rec)
    if not lines:
        return None
    if any(d.get("type") in ("session_meta", "response_item") for d in lines[:50]):
        return _codex(lines, path)
    if any("sessionId" in d or d.get("type") in ("user", "assistant") for d in lines[:50]):
        return _claude(lines, path)
    return _generic(lines, path)


def read_part(path: Path, max_bytes: int, from_end: bool = True) -> Transcript | None:
    """Parse only the start or the end of a (possibly huge) transcript."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if from_end:
                fh.seek(max(0, size - max_bytes))
            raw = fh.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = raw.split("\n")  # not splitlines(): JSON strings may hold a raw U+2028
    if from_end and size > max_bytes:
        lines = lines[1:]
    elif not from_end and size > max_bytes:
        lines = lines[:-1]
    return parse_lines(lines, path)
