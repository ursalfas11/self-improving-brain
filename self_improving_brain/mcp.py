"""The brain as an MCP server: every MCP client gets lessons, recall, guards and handoffs.

Claude Code has hooks, so the brain reaches it on its own. Cursor, Windsurf, Claude Desktop,
VS Code, Gemini CLI, Codex, Zed and Cline speak MCP instead: `brain mcp` serves the same brain
over stdio (JSON-RPC 2.0, one message per line). `brain mcp install <client>` writes the entry
into that client's config; `brain mcp config` prints it for any other client.

The tools can read, search, record drafts and handoffs. They can never approve, trust or promote
a lesson: that stays a command the user runs.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from . import __version__, handoff, hooks, inbox, learn
from .config import Vault, open_vault
from .util import append_jsonl, digest, now_iso, short

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """This server is the user's self-improving brain: lessons learned from their corrections, notes, guards.
- At the start of every task call brain_context with the task in one sentence. Follow every LESSON it returns; they outrank your defaults.
- Before an irreversible action (force push, deleting data, deploys, publishing) call brain_check with the command. If it returns GUARD, ask the user first.
- When the user corrects you, call brain_correction with what went wrong, so the brain can learn it (the user approves every rule).
- When the user says "continue" or "where were we", call brain_resume. Before you stop a long task, call brain_handoff.
- Look things up with brain_search and brain_read before guessing about the user's projects."""


def _s(**props) -> dict:
    req = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def _str(desc: str, optional: bool = False) -> dict:
    return {"type": "string", "description": desc, "optional": optional}


TOOLS = [
    ("brain_context", "Lessons and relevant notes for the task at hand. Call it first, with the task in one sentence.",
     _s(task=_str("what you are about to do, in one sentence", True))),
    ("brain_search", "Find the note sections that answer a question (BM25 over the user's vault).",
     _s(query=_str("the question"), k={"type": "integer", "description": "how many hits (default 5)", "optional": True})),
    ("brain_read", "Read a note's outline, or one section of it.",
     _s(note=_str("note name from a search hit"), section=_str("section title", True))),
    ("brain_check", "Would this action trip one of the user's guard lessons? Call before irreversible commands.",
     _s(command=_str("the shell command", True), path=_str("a file you are about to write", True),
        tool=_str("tool name, if not a shell command", True))),
    ("brain_correction", "Record that the user corrected you. Becomes a lesson draft the user reviews; nothing is approved.",
     _s(what_happened=_str("what you did and what the user said, in their words where possible"),
        rule=_str("the rule you would propose, one sentence", True))),
    ("brain_remember", "Put one fact, decision, status, pitfall, preference or todo into the inbox; it is distilled into the notes later.",
     _s(type={"type": "string", "enum": [t for t in inbox.TYPES if t != "lesson"]}, claim=_str("one sentence"),
        note=_str("the note it belongs to, if you know it", True))),
    ("brain_resume", "The last handoff: the user's own words, the open question, the files in flight.",
     _s(any_repo={"type": "boolean", "description": "not only this repo", "optional": True})),
    ("brain_handoff", "Leave a handoff before you stop, so the next session (any agent) continues where you are.",
     _s(goal=_str("what the user wants"), user_words=_str("the user's last instructions, verbatim"),
        done=_str("what is finished", True), next=_str("the next step", True),
        open_question=_str("a question still waiting for the user", True),
        files=_str("files you changed, comma separated", True))),
    ("brain_stats", "Is the agent getting better? Corrections per 100 messages, lesson health, recall precision.", _s()),
]


def _cli(v: Vault, *argv: str) -> tuple[int, str]:
    from . import cli
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cli.main(["--vault", str(v.root), *argv])
    except SystemExit as e:
        rc = int(e.code or 0) if isinstance(e.code, int) else 2
    return rc, buf.getvalue().strip()


class Server:
    def __init__(self, v: Vault, cwd: str = ""):
        self.v = v
        self.cwd = cwd or os.getcwd()
        self.session = os.environ.get("BRAIN_SESSION") or f"mcp-{os.getpid()}"
        os.environ["BRAIN_SESSION"] = self.session
        self.started = False

    # --- tools ------------------------------------------------------------------------------
    def call(self, name: str, args: dict) -> str:
        v = self.v
        if name == "brain_context":
            parts = []
            if not self.started:
                out = hooks.session_start(v, {"session_id": self.session, "cwd": self.cwd, "source": "startup"})
                parts.append(out.get("hookSpecificOutput", {}).get("additionalContext", ""))
                self.started = True
            if args.get("task"):
                out = hooks.prompt(v, {"session_id": self.session, "prompt": str(args["task"])})
                parts.append(out.get("hookSpecificOutput", {}).get("additionalContext", ""))
            text = "\n".join(p for p in parts if p)
            return text or "No lesson applies to this task. brain_search finds notes if you need background."
        if name == "brain_search":
            return _cli(v, "search", str(args["query"]), "-k", str(int(args.get("k") or 5)))[1]
        if name == "brain_read":
            return _cli(v, "read", str(args["note"]), *([str(args["section"])] if args.get("section") else []))[1]
        if name == "brain_check":
            argv = ["check"]
            for k in ("command", "path", "tool"):
                if args.get(k):
                    argv += [f"--{k}", str(args[k])]
            return _cli(v, *argv)[1]
        if name == "brain_correction":
            what = str(args["what_happened"]).strip()
            if not what:
                raise ValueError("what_happened is empty")
            d = {"id": "d-" + digest(self.session, what, now_iso())[:8], "t": now_iso(), "kind": "lesson",
                 "status": "open", "score": 0.6, "quote": short(what, 500), "context": "(reported by the agent over MCP)",
                 "session": self.session, "source": "mcp", "ts": now_iso(), "rule": short(str(args.get("rule") or ""), 240)}
            append_jsonl(learn.drafts_path(v), d)
            return f"✓ draft {d['id']} — the user turns it into a lesson with `brain review` (nothing is approved yet)."
        if name == "brain_remember":
            argv = ["remember", str(args["type"]), str(args["claim"])]
            if args.get("note"):
                argv += ["--note", str(args["note"])]
            return _cli(v, *argv)[1]
        if name == "brain_resume":
            return _cli(v, "resume", *(["--any"] if args.get("any_repo") else []))[1]
        if name == "brain_handoff":
            files = [f.strip() for f in str(args.get("files") or "").split(",") if f.strip()]
            words = str(args["user_words"]).strip()
            h = {"session": self.session, "agent": "mcp", "cwd": self.cwd, "repo": hooks.repo_of(self.cwd), "t": now_iso(),
                 "goal": short(str(args["goal"]), 500), "user_words": [short(words, 700)] if words else [],
                 "changed": files[-12:], "read": [], "commands": [], "git": handoff._git(self.cwd),
                 "last_answer": short(" ".join(x for x in (
                     f"Done: {args['done']}." if args.get("done") else "",
                     f"Next: {args['next']}." if args.get("next") else "",
                     f"Open question: {args['open_question']}" if args.get("open_question") else "") if x), 900),
                 "waiting": bool(args.get("open_question"))}
            handoff.save(v, h)
            return "✓ handoff saved — the next session gets it with brain_resume or `brain resume`."
        if name == "brain_stats":
            return _cli(v, "stats")[1]
        raise KeyError(name)

    # --- protocol ---------------------------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # a notification: never answered
            return None
        try:
            if method == "initialize":
                want = str((msg.get("params") or {}).get("protocolVersion") or "")
                result = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[1],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "self-improving-brain", "version": __version__},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s in TOOLS]}
            elif method == "tools/call":
                p = msg.get("params") or {}
                name = str(p.get("name") or "")
                if name not in {n for n, _, _ in TOOLS}:
                    return _err(mid, -32602, f"unknown tool {name}")
                try:
                    with redirect_stdout(io.StringIO()):  # stdout is the protocol channel: nothing stray may reach it
                        text = self.call(name, p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text or "(empty)"}], "isError": False}
                except Exception as e:  # a failing tool is a tool result, not a dead server
                    result = {"content": [{"type": "text", "text": f"error: {type(e).__name__}: {e}"}], "isError": True}
            elif method in ("resources/list", "prompts/list"):
                result = {method.split("/")[0]: []}
            else:
                return _err(mid, -32601, f"method not found: {method}")
        except Exception as e:  # never let one bad request kill the server
            return _err(mid, -32603, f"internal error: {e}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}


def serve(v: Vault, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    srv = Server(v)
    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = _err(None, -32700, "parse error")
        else:
            if isinstance(msg, list):
                out = [r for r in (srv.handle(m) for m in msg if isinstance(m, dict)) if r] or None
            else:
                out = srv.handle(msg) if isinstance(msg, dict) else _err(None, -32600, "invalid request")
        if out:
            stdout.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()
    return 0


# --- client configs -----------------------------------------------------------------------

def server_entry(v: Vault) -> dict:
    from .adapters import brain_argv
    argv = brain_argv() + ["--vault", str(v.root), "mcp"]
    return {"command": argv[0], "args": argv[1:]}


def _appdata(*parts: str) -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home.joinpath("Library", "Application Support", *parts)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming").joinpath(*parts)
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config").joinpath(*parts)


def client_path(client: str) -> Path:
    home = Path.home()
    return {
        "claude-desktop": _appdata("Claude", "claude_desktop_config.json"),
        "cursor": home / ".cursor" / "mcp.json",
        "windsurf": home / ".codeium" / "windsurf" / "mcp_config.json",
        "gemini": home / ".gemini" / "settings.json",
        "vscode": _appdata("Code", "User", "mcp.json"),
        "codex": home / ".codex" / "config.toml",
    }[client]


CLIENTS = ("claude-desktop", "cursor", "windsurf", "gemini", "vscode", "codex")


def snippet(v: Vault, client: str = "") -> str:
    e = server_entry(v)
    if client == "codex":
        return "[mcp_servers.brain]\n" + f"command = {json.dumps(e['command'])}\nargs = {json.dumps(e['args'])}\n"
    if client == "vscode":
        return json.dumps({"servers": {"brain": {"type": "stdio", **e}}}, indent=2)
    if client == "zed":
        return json.dumps({"context_servers": {"brain": {"command": {"path": e["command"], "args": e["args"]}}}}, indent=2)
    return json.dumps({"mcpServers": {"brain": e}}, indent=2)


def install(v: Vault, client: str) -> str:
    """Add (or update) the `brain` entry in one client's config. Everything else in the file stays."""
    p = client_path(client)
    e = server_entry(v)
    if client == "codex":
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if "[mcp_servers.brain]" in text:
            return f"{p} already has [mcp_servers.brain] — left as it is"
        _backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.rstrip("\n") + ("\n\n" if text.strip() else "") + snippet(v, "codex"), encoding="utf-8")
        return f"✓ {p}"
    data = {}
    if p.exists() and p.read_text(encoding="utf-8").strip():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(f"{p} is not plain JSON (comments?) — add this by hand:\n{snippet(v, client)}")
        if not isinstance(data, dict):
            raise ValueError(f"{p} is not a JSON object")
    key = "servers" if client == "vscode" else "mcpServers"
    entry = {"type": "stdio", **e} if client == "vscode" else e
    if data.get(key, {}).get("brain") == entry:
        return f"{p} is already up to date"
    _backup(p)
    data.setdefault(key, {})["brain"] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return f"✓ {p}"


def uninstall(client: str) -> str:
    p = client_path(client)
    if not p.exists():
        return f"{p} does not exist"
    if client == "codex":
        lines, out, skip = p.read_text(encoding="utf-8").splitlines(), [], False
        for line in lines:
            if line.strip().startswith("["):
                skip = line.strip() == "[mcp_servers.brain]"
            if not skip:
                out.append(line)
        _backup(p)
        p.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        return f"✓ {p}"
    data = json.loads(p.read_text(encoding="utf-8") or "{}")
    key = "servers" if client == "vscode" else "mcpServers"
    if "brain" not in data.get(key, {}):
        return f"{p} has no brain entry"
    _backup(p)
    del data[key]["brain"]
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"✓ {p}"


def _backup(p: Path) -> None:
    if p.exists():
        bak = p.with_name(p.name + ".brain-bak")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())


def detected() -> list[str]:
    """MCP clients that look installed on this machine."""
    out = []
    for c in CLIENTS:
        p = client_path(c)
        if p.exists() or p.parent.is_dir():
            out.append(c)
    return out


def main(vault_arg: str | None) -> int:
    return serve(open_vault(vault_arg))
