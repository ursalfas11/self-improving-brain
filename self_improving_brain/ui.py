"""`brain ui`: the brain in your browser. Local only, one page, no dependencies.

The server binds 127.0.0.1 and answers only requests that carry the random token from the URL it
opens, and only for its own Host header, so no web page you visit can read your vault or click
"undo" for you. Everything the page shows comes from one JSON call (`/api/state`).
"""
from __future__ import annotations

import json
import secrets as pysecrets
import socketserver
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, events, handoff, inbox, journal, llm, metrics, nightly, replay, trust
from .config import Vault
from .notes import Library
from .util import load_json

PAGE = Path(__file__).parent / "ui.html"
DEMO = Path(__file__).parent / "ui_demo.json"


def demo_note(name: str) -> dict | None:
    """`brain ui --demo` reads the starter vault and the demo file, never yours."""
    from .adapters import TEMPLATES
    extra = json.loads(DEMO.read_text(encoding="utf-8")).get("notes") or {}
    if name in extra:
        return extra[name]
    for root in (TEMPLATES / "vault",):
        for p in root.rglob("*.md"):
            text = p.read_text(encoding="utf-8").replace("{{TODAY}}", "2026-10-30")
            from .notes import parse_frontmatter
            meta, body = parse_frontmatter(text)
            if str(meta.get("name") or p.stem) == name:
                return {"name": name, "rel": p.relative_to(root).as_posix(), "description": str(meta.get("description", "")),
                        "type": str(meta.get("type", "")), "body": body}
    return None


def state(v: Vault) -> dict:
    """Everything the page shows, in one object (also what `brain report` renders)."""
    lib = Library(v.root)
    lessons = lib.lessons()
    silent = trust.untrusted(v, lessons)
    silent_ids = {l.id: why for l, why in silent}
    health = {h["id"]: h for h in metrics.lesson_health(v, lib)}
    names = set(lib.notes)
    curve = metrics.weekly_curve(v, 12)
    night = load_json(v.path("state", "nightly.json"), {})
    learned = []
    for e in journal.recent(v, days=30):
        what, text, why = journal.describe(e)
        learned.append({"id": e["id"], "t": e.get("t", ""), "kind": e.get("kind"), "what": what, "text": text,
                        "why": why, "lesson": e.get("lesson", ""), "note": e.get("note", "")})
    return {
        "vault": {"root": str(v.root), "name": v.root.name, "version": __version__, "notes": len(lib.notes),
                  "lessons": len(lessons), "inbox": len(inbox.items(v)), "learning": llm.available(v)},
        "digest": metrics.week_digest(v),
        "since_start": metrics.since_start(metrics.weekly_curve(v, 104), installed=metrics.installed(v)),
        "curve": curve,
        "efficiency": metrics.efficiency(v),
        "recall": metrics.recall_numbers(v),
        "nightly": {"t": night.get("t", ""), "summary": nightly.summary(night) if night.get("t") else "",
                    "attention": night.get("attention") or []},
        "lessons": [{"id": l.id, "note": l.note, "rule": l.rule, "mechanism": l.mechanism, "tools": l.tools,
                     "commands": l.commands, "paths": l.paths, "words": l.words, "check": l.check, "since": l.since,
                     "silent": silent_ids.get(l.id), "health": health.get(l.id, {})} for l in lessons],
        "learned": learned,
        "silent": [{"id": l.id, "rule": l.rule, "note": l.note, "why": why} for l, why in silent],
        "replay": replay.last(v),
        "checks": events.check_counts(v),
        "handoffs": list(reversed(handoff.load_index(v)))[:10],
        "graph": {"nodes": [{"id": n.name, "rel": n.rel, "folder": n.rel.split("/")[0] if "/" in n.rel else "",
                             "type": n.type, "description": n.description, "lessons": len([l for l in n.lessons if not l.retired]),
                             "hub": n.is_hub} for n in lib.notes.values()],
                  "links": [[n.name, t] for n in lib.notes.values() for t in n.links if t in names]},
    }


def note(v: Vault, name: str) -> dict | None:
    n = Library(v.root).find(name)
    if n is None:
        return None
    return {"name": n.name, "rel": n.rel, "description": n.description, "type": n.type, "body": n.body}


def act(v: Vault, path: str, body: dict) -> dict:
    """The page's buttons. Each is one CLI command the user could have typed."""
    if path == "/api/undo":
        return {"ok": True, "message": journal.undo(v, str(body["id"]))}
    if path == "/api/trust":
        lib = Library(v.root)
        ls = [l for l in lib.lessons() if l.id == str(body["id"])]
        if not ls:
            raise KeyError(f"no lesson {body['id']}")
        trust.record(v, ls, "ui")
        return {"ok": True}
    if path == "/api/replay":
        r = replay.run(v, days=int(body.get("days") or 30))
        return {"ok": True, "replay": r}
    raise KeyError(path)


def make_handler(v: Vault | None, token: str, port_ref: list):
    class H(BaseHTTPRequestHandler):
        server_version = "self-improving-brain"

        def log_message(self, *a):  # quiet terminal
            pass

        def _send(self, code: int, data, ctype="application/json"):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def _allowed(self, q: dict) -> bool:
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"):
                return False  # DNS rebinding: another name pointing at 127.0.0.1
            given = self.headers.get("X-Brain-Token") or (q.get("t") or [""])[0]
            return pysecrets.compare_digest(given, token)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._allowed(q):
                if u.path == "/":
                    return self._send(403, b"<!doctype html><meta charset=utf-8><title>brain ui</title><body style=\"font:16px "
                                      b"system-ui;padding:2em\">Open the link that <code>brain ui</code> printed in your "
                                      b"terminal.", "text/html")
                return self._send(403, {"error": "open the link `brain ui` printed"})
            try:
                if u.path == "/":
                    return self._send(200, PAGE.read_bytes(), "text/html")
                if u.path == "/api/state":
                    return self._send(200, state(v) if v else json.loads(DEMO.read_text(encoding="utf-8")))
                if u.path == "/api/note":
                    name = (q.get("name") or [""])[0]
                    n = note(v, name) if v else demo_note(name)
                    return self._send(200 if n else 404, n or {"error": "no such note"})
            except Exception as e:  # noqa: BLE001 — show it in the page, keep serving
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._allowed({}) or self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port_ref[0]}",
                                                                           f"http://localhost:{port_ref[0]}"):
                return self._send(403, {"error": "forbidden"})
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = json.loads(self.rfile.read(n) or b"{}")
                if v is None:
                    return self._send(200, {"ok": True, "demo": True})
                return self._send(200, act(v, u.path, body if isinstance(body, dict) else {}))
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": str(e).strip("'\"")})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return H


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer looks up the host's FQDN here, which can take seconds on some networks
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(v: Vault | None, port: int = 0, open_browser: bool = True, ready=None) -> int:
    """v=None serves the demo data (`brain ui --demo`), for screenshots and trying it out."""
    token = pysecrets.token_urlsafe(18)
    port_ref = [port]
    httpd = _Server(("127.0.0.1", port), make_handler(v, token, port_ref))
    port_ref[0] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}/?t={token}"
    if ready:
        ready(url, httpd)
    else:
        print(f"🧠  {url}\n   (local only · Ctrl-C stops it)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
