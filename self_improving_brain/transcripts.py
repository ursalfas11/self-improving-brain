"""Read agent session transcripts into one neutral shape.

Supported out of the box: Claude Code (~/.claude/projects/**/*.jsonl) and Codex CLI
(~/.codex/sessions/**/*.jsonl). Other agents: write a parser that yields the same Turn shape
(PRs welcome), or export your chats as JSONL lines {"role": "user"|"assistant", "text": ...}.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REJECT_MARKERS = (
    "doesn't want to proceed", "does not want to proceed", "user rejected", "tool use was rejected",
    "user denied", "permission denied by user", "the user declined",
)


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

    @property
    def user_messages(self) -> list[Turn]:
        return [t for t in self.turns if t.role == "user"]


def _is_noise(text: str) -> bool:
    s = text.lstrip()
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
    session = ""
    for d in lines:
        session = session or d.get("sessionId", "")
        t, msg, ts = d.get("type"), d.get("message"), str(d.get("timestamp") or "")
        if not isinstance(msg, dict) or d.get("isSidechain") or d.get("isMeta"):
            continue
        content = msg.get("content")
        if t == "user":
            if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        txt = _text_of(c.get("content", ""))
                        rej = bool(c.get("is_error")) and any(m in txt.lower() for m in REJECT_MARKERS)
                        turns.append(Turn("tool_result", txt[:2000], rejected=rej, ts=ts))
                continue
            txt = _text_of(content)
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
    return Transcript(path, session or path.stem, "claude", turns)


def _codex(lines: list[dict], path: Path) -> Transcript:
    turns: list[Turn] = []
    session = ""
    for d in lines:
        p, ts = d.get("payload") or {}, str(d.get("timestamp") or "")
        if d.get("type") == "session_meta":
            session = p.get("id") or p.get("session_id") or session
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
            rej = any(m in txt.lower() for m in REJECT_MARKERS)
            turns.append(Turn("tool_result", txt[:2000], rejected=rej, ts=ts))
    return Transcript(path, session or path.stem, "codex", turns)


def _generic(lines: list[dict], path: Path) -> Transcript:
    turns = [Turn(d["role"], d.get("text", ""), ts=d.get("ts", "")) for d in lines
             if d.get("role") in ("user", "assistant") and isinstance(d.get("text"), str)]
    return Transcript(path, path.stem, "generic", turns)


def parse(path: Path) -> Transcript | None:
    lines = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    lines.append(rec)
    except OSError:
        return None
    if not lines:
        return None
    if any(d.get("type") in ("session_meta", "response_item") for d in lines[:50]):
        return _codex(lines, path)
    if any("sessionId" in d or d.get("type") in ("user", "assistant") for d in lines[:50]):
        return _claude(lines, path)
    return _generic(lines, path)
