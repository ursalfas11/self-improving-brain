"""Dense recall: embeddings from a model on your own machine, never from a service.

Optional and off unless a local `ollama` answers and has the model `bge-m3` (`ollama pull bge-m3`). Without it
everything else runs as before. Only the loopback address is ever contacted: an OLLAMA_HOST that points to another
machine is ignored, so no note leaves this computer.

  night   `build` embeds every note whole and per section; a note whose text did not change keeps its vectors.
  prompt  the question is embedded with a short deadline (0.35 s). If ollama does not answer in time, nothing is
          asked again for five minutes, so a sleeping model costs one deadline, not one per prompt.

The vectors live in the cache directory (outside the vault, never synced). Any failure means: no dense signal.
"""
from __future__ import annotations

import array
import http.client
import json
import math
import os
import pickle
import re
import time
from pathlib import Path

from . import cache
from .config import Vault
from .fuzzy import CANDIDATES, note_sig, top

MODEL = "bge-m3"  # the one that was measured; another model is not guessed
DEADLINE = 0.35
PAUSE_S = 300
NOTE_CHARS, SECTION_CHARS = 6000, 1800
_FP = f"dense-1:{MODEL}"
_LOOPBACK = ("localhost", "::1", "127.0.0.1")
_mem: list = [None, None]  # (mtime, docs) per process


def endpoint() -> tuple[str, int] | None:
    """(host, port) of the local ollama, None when OLLAMA_HOST names another machine."""
    h = re.sub(r"^https?://", "", (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()).split("/")[0]
    if h.startswith("["):  # [::1]:11434
        host, _, rest = h[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = h.partition(":") if h.count(":") == 1 else (h, "", "")
    if host in ("0.0.0.0", ""):  # a bind address, not a place to connect to
        host = "127.0.0.1"
    if host not in _LOOPBACK and not host.startswith("127."):
        return None
    try:
        return host, int(port or 11434)
    except ValueError:
        return None


def _call(method: str, path: str, body: dict | None, deadline: float):
    ep = endpoint()
    if ep is None:
        return None
    c = http.client.HTTPConnection(ep[0], ep[1], timeout=deadline)
    try:
        c.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        return json.loads(data.decode("utf-8", "replace")) if r.status == 200 else None
    finally:
        c.close()


def model_available(deadline: float = 1.0) -> bool:
    try:
        d = _call("GET", "/api/tags", None, deadline) or {}
        return any((m.get("name") or "").split(":")[0] == MODEL for m in d.get("models") or [])
    except Exception:  # noqa: BLE001
        return False


def embed(texts: list[str], deadline: float) -> list[array.array] | None:
    """Unit-length float vectors, or None. Raises on a dead connection: the caller decides what that means."""
    d = _call("POST", "/api/embed", {"model": MODEL, "input": texts, "keep_alive": "30m", "truncate": True}, deadline)
    if not d or not isinstance(d.get("embeddings"), list) or len(d["embeddings"]) != len(texts):
        return None
    out = []
    for v in d["embeddings"]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append(array.array("f", (x / n for x in v)))
    return out


def _note_text(n) -> str:
    heads = " · ".join(s.title for s in n.sections if s.title != "(intro)")
    return f"{n.name.replace('-', ' ')}\n{n.description}\n{heads}\n{n.body}"[:NOTE_CHARS]


def _sections(n) -> list[str]:
    return [f"{n.name.replace('-', ' ')}\n{s.title}\n{s.body.strip()}"[:SECTION_CHARS]
            for s in n.sections if s.title != "(intro)" and len(s.body.strip()) >= 80]


def _path(v: Vault) -> Path:
    return cache._path(v, "dense")


def _docs(v: Vault) -> dict | None:
    p = _path(v)
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        return None
    if _mem[0] == (str(p), m):
        return _mem[1]
    docs = cache._load(v, "dense", _FP)
    _mem[:] = [(str(p), m), docs if isinstance(docs, dict) else None]
    return _mem[1]


def build(v: Vault, lib, deadline: float = 120.0) -> dict | str:
    """Nightly. -> {"notes": n, "new": k} or a short reason why the signal is off."""
    if endpoint() is None:
        return "off (OLLAMA_HOST is not this machine)"
    if not model_available():
        return f"off (no local ollama with {MODEL})"
    old = cache._load(v, "dense", _FP) or {}
    docs: dict = {}
    new = 0
    try:
        for name, n in sorted(lib.notes.items()):
            if n.is_hub:
                continue
            sig = note_sig(n)
            if name in old and old[name][0] == sig:
                docs[name] = old[name]
                continue
            vs = embed([_note_text(n)] + _sections(n), deadline)
            if vs is None:
                return "off (ollama gave no vectors)"
            docs[name] = (sig, vs[0].tobytes(), [x.tobytes() for x in vs[1:]])
            new += 1
    except Exception as e:  # noqa: BLE001
        return f"off ({type(e).__name__})"
    cache._store(v, "dense", _FP, docs)
    _mem[:] = [None, None]
    return {"notes": len(docs), "new": new}


def _paused(v: Vault) -> bool:
    try:
        return time.time() - _path(v).with_suffix(".pause").stat().st_mtime < PAUSE_S
    except OSError:
        return False


def _pause(v: Vault) -> None:
    try:
        p = _path(v).with_suffix(".pause")
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        p.write_text(str(int(time.time())), encoding="ascii")
    except OSError:
        pass


def scores(v: Vault, lib, query: str, extra: set | frozenset = frozenset(),
           deadline: float = DEADLINE) -> dict[str, float]:
    """{note: cosine (half the whole note, half the best section)} or {}. The section half is computed only for the
    best notes of every signal (`extra`) and of this one; the rest counts with the note vector alone, a little lower."""
    docs = _docs(v)
    if not docs or _paused(v):
        return {}
    try:
        q = embed([query[:600]], deadline)
    except Exception:  # noqa: BLE001
        q = None
    if not q:
        _pause(v)
        return {}
    q = q[0]

    def dot(raw: bytes) -> float:
        a = array.array("f")
        a.frombytes(raw)
        return sum(x * y for x, y in zip(q, a))

    whole = {n: dot(d[1]) for n, d in docs.items() if n in lib.notes}
    cand = set(top(whole, CANDIDATES)) | set(extra)
    out = {}
    for n, s in whole.items():
        secs = docs[n][2]
        out[n] = 0.5 * s + 0.5 * max(dot(x) for x in secs) if n in cand and secs else s - 0.05
    return out
