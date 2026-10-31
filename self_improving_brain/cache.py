"""Keep hooks fast: parse the vault once, reuse it until a note changes.

The cache key is a fingerprint of every note's path, size and mtime (one os.walk, no reads).
The cache lives in ~/.cache/self-improving-brain/ (never inside the vault) and is rebuilt transparently.
"""
from __future__ import annotations

import functools
import hashlib
import os
import pickle
from pathlib import Path

from .config import Vault
from .notes import Library, iter_note_paths

VERSION = 4


@functools.lru_cache(maxsize=1)
def _code_stamp() -> str:
    """Cached objects are pickled classes built by this package's code (tokenizer, parser, linter, secret
    patterns): any change to any module must invalidate the cache."""
    from . import __version__
    stamps = []
    for f in sorted(Path(__file__).resolve().parent.glob("*.py")):
        try:
            stamps.append(f"{f.name}={f.stat().st_mtime_ns}")
        except OSError:
            stamps.append(f"{f.name}=?")
    return f"{VERSION}:{__version__}:" + ":".join(stamps)


def fingerprint(root: Path) -> str:
    h = hashlib.sha1(_code_stamp().encode())
    for p in iter_note_paths(root):
        try:
            st = p.stat()
        except OSError:
            continue
        # fsencode: a file name that is not valid UTF-8 (possible on Linux) must not break every hook
        h.update(os.fsencode(str(p)) + f"\0{st.st_size}\0{st.st_mtime_ns}\0{st.st_ctime_ns}\0{st.st_ino}\n".encode())
    return h.hexdigest()


def base_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "self-improving-brain"


def _cache_dir(v: Vault) -> Path:
    """Outside the vault on purpose: a vault cloned from elsewhere can never ship a poisoned cache."""
    return base_dir() / hashlib.sha1(str(v.root).encode()).hexdigest()[:16]


def _path(v: Vault, name: str) -> Path:
    return _cache_dir(v) / f"{name}.cache"


def _load(v: Vault, name: str, fp: str):
    try:
        with _path(v, name).open("rb") as fh:
            stored_fp, obj = pickle.load(fh)
        return obj if stored_fp == fp else None
    except Exception:  # noqa: BLE001 — a broken cache is just a cache miss
        return None


def _store(v: Vault, name: str, fp: str, obj) -> None:
    p = _path(v, name)
    try:
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # holds the full text of your notes
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            pickle.dump((fp, obj), fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, p)
    except OSError:
        pass


def library(v: Vault, fp: str | None = None) -> Library:
    fp = fp or fingerprint(v.root)
    lib = _load(v, "library", fp)
    if lib is None:
        lib = Library(v.root)
        _store(v, "library", fp, lib)
    return lib


def lessons(v: Vault):
    """Every lesson in the vault — the hot path for every tool call."""
    fp = fingerprint(v.root)
    ls = _load(v, "lessons", fp)
    if ls is None:
        ls = library(v, fp).lessons()
        _store(v, "lessons", fp, ls)
    return ls


def index(v: Vault, learned: dict):
    """Library + BM25 index, cached per vault state. The learned model is applied on top at query time."""
    from .search import Index
    fp = fingerprint(v.root)
    idx = _load(v, "index", fp)
    if idx is None:
        idx = Index(library(v, fp), {})
        _store(v, "index", fp, idx)
    idx.learned = learned
    return idx


def must_findings(v: Vault) -> list:
    from .lint import lint_note
    fp = fingerprint(v.root)
    found = _load(v, "must", fp)
    if found is None:
        lib = library(v, fp)
        found = [str(f) for n in lib.notes.values() for f in lint_note(n, lib) if f.level == "MUST"]
        _store(v, "must", fp, found)
    return found
