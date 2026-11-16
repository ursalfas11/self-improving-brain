"""Notes that fall behind GitHub: "PR acme/api#12 is open" stays in a note long after the merge.

Every night the brain collects the pull requests and issues your notes mention, asks GitHub for their state
(one batched call through your `gh` login, cached) and files a claim for every line that still calls a merged
or closed one open. The claim goes the way every fact goes — inbox, distill, journal — so the line is
corrected by the same writer, shows up in `brain review` and can be undone.

What leaves your machine: owner, repository name and number of the references your notes contain, to GitHub,
with your own login. Nothing else. Off: `brain config github_check false`.

The parsing and the GitHub call live in github_refs.py (shared with other engines); this file is the part
that knows a vault.
"""
from __future__ import annotations

from . import github_refs as gh
from . import inbox
from .config import Vault
from .notes import Library, Note
from .util import load_json, now_iso, save_json

STRONG = ("url", "full", "short", "list")  # how sure the repository of a reference is (see github_refs)


def state_path(v: Vault):
    return v.path("state", "github.json")


def load(v: Vault) -> dict:
    st = load_json(state_path(v), {})
    st = st if isinstance(st, dict) else {}
    for k in ("items", "repos", "filed"):
        if not isinstance(st.get(k), dict):
            st[k] = {}
    return st


def repo_map(v: Vault) -> dict:
    """Short names -> owner/repo: what you configured wins over what the clones on this machine say."""
    out = gh.repo_map_from_clones(v.get("repo_roots") or [])
    conf = v.get("github_repos") or {}
    if isinstance(conf, dict):
        out.update({str(k).lower(): str(val) for k, val in conf.items() if "/" in str(val)})
    return out


def default_repo(n: Note, repos: dict) -> str:
    """A note that declares `repo:` is about that repository: a bare #12 in it means that one."""
    raw = n.meta.get("repo")
    names = [str(x).strip() for x in (raw if isinstance(raw, list) else [raw]) if x]
    if len(names) != 1:
        return ""
    return names[0] if "/" in names[0] else repos.get(names[0].lower(), "")


def _text(n: Note) -> str:
    return n.path.read_text(encoding="utf-8", errors="replace")


def findings(v: Vault, lib: Library, st: dict | None = None, weak: bool = True) -> list[tuple[Note, dict]]:
    """(note, stale line) from what is cached: no network, so lint and the hooks may call it."""
    st = st or load(v)
    if not st["items"]:
        return []
    out = []
    for n in lib.notes.values():
        if "#" not in n.body and "github.com" not in n.body:
            continue
        for f in gh.stale_lines(_text(n), st["items"], st["repos"], default_repo(n, st["repos"]), weak=weak):
            out.append((n, f))
    return out


def run(v: Vault, lib: Library | None = None, runner=None) -> dict | str:
    """The nightly step. -> what it did, or why it did nothing."""
    if not v.get("github_check"):
        return "off"
    if runner is None and not gh.gh_available():
        return "no gh CLI"
    lib = lib or Library(v.root)
    st = load(v)
    st["repos"] = repo_map(v)
    keys = set()
    for n in lib.notes.values():
        if "#" not in n.body and "github.com" not in n.body:
            continue
        for r in gh.find_refs(_text(n), st["repos"], default_repo(n, st["repos"])):
            keys.add(gh.key(r["repo"], r["n"]))
    # what no note mentions any more is forgotten, so the cache does not grow for ever
    st["items"] = {k: e for k, e in st["items"].items() if k in keys}
    rep = gh.fetch_states(keys, st["items"], run=runner)
    stale = findings(v, lib, st)
    lines: dict = {}  # one claim per line: "open: api#1, api#2" is one statement, corrected in one edit
    for n, f in stale:
        if f["how"] in STRONG:  # a guessed repository is shown by lint, never written on its own
            lines.setdefault((n.name, f["line_no"]), []).append(f)
    filed = 0
    for (name, _), fs in sorted(lines.items()):
        marks = [f"{name}|{f['ref']}|{f['state']}" for f in fs]
        if all(m in st["filed"] for m in marks):
            continue  # said once: a correction you took back is not made again
        try:
            inbox.add(v, "status", gh.claim(fs, name), source="github", quote=fs[0]["line"], note=name)
            filed += 1
        except inbox.Rejected as e:
            if str(e).startswith("already in inbox"):
                continue  # reads like a claim that still waits: filed once that one is distilled, not lost
            # anything else: the line holds something that must not travel; lint keeps showing it
        for m in marks:
            st["filed"][m] = now_iso()[:10]
    st["t"] = now_iso()
    save_json(state_path(v), st)
    out = {"refs": len(keys), "asked": rep["asked"], "stale": len(stale), "filed": filed}
    if rep["errors"]:
        out["errors"] = rep["errors"][:3]
    return out
