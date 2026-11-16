"""GitHub state for the pull requests and issues a vault mentions.

A note that says "PR acme/api#12 is open" goes stale the moment that PR is merged. This module finds such
references, asks GitHub for their state (through the `gh` CLI, batched and cached) and names the lines that
still call a merged or closed item open.

  repo_map_from_clones(roots)   short repo names -> owner/repo, read from the git clones on this machine
  find_refs(text, repo_map)     every PR or issue reference in a note, with its line
  fetch_states(keys, cache)     their state from GitHub: one GraphQL call per 60 references, cached
  stale_lines(text, cache, …)   the lines that call a merged or closed item open
  claim(finding, lang)          one self-contained sentence about such a line, for the inbox

SHARED FILE: it is kept byte-identical in the public self-improving-brain package and in private vault
tooling. It imports nothing but the standard library and knows neither engine. Change it in one place,
copy it to the other. Python 3.8 and later.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess

VERSION = 1

URL_RX = re.compile(r"https?://github\.com/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)/(?:pull|issues)/(\d+)")
FULL_RX = re.compile(r"(?<![\w./-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)#(\d+)\b")
SHORT_RX = re.compile(r"(?<![\w./-])([A-Za-z0-9][\w.-]*)[ \t]?#(\d+)\b")
BARE_RX = re.compile(r"(?<![\w/#&-])#(\d+)\b")
# "repo #12 (…), #14 and #15": a bare number continues the list of the reference before it
LIST_GAP = re.compile(r"(?:,|;|\bund\b|\band\b|\bsowie\b|&|\+|/)\s*(?:PRs?\s*|Issues?\s*)?[`*_(]*$", re.I)
TOKEN_RX = re.compile(r"#\d+\b|https?://github\.com/\S+?/(?:pull|issues)/\d+")
CONFIG_URL = re.compile(r"^\s*url\s*=\s*\S*github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?\s*$", re.M)

# what a sentence says about the thing it mentions. German and English, because notes are written in both.
NOT_DONE = re.compile(
    r"\b(?:noch\s+)?nicht\s+(?:gemergt|gemerged|zusammengeführt|geschlossen|erledigt|fertig|live|ausgerollt)\b"
    r"|\bnot\s+(?:yet\s+)?(?:merged|closed|done|live|deployed|released)\b|\bunmerged\b|\bungemergt\b", re.I)
# of those, the ones that only say "no merge": true of a pull request that was closed without one
NOT_MERGED = re.compile(r"\b(?:noch\s+)?nicht\s+(?:gemergt|gemerged|zusammengeführt)\b|\bnot\s+(?:yet\s+)?merged\b"
                        r"|\bunmerged\b|\bungemergt\b", re.I)
OPEN = re.compile(
    r"(?<![\w-])(?:offen(?:e[rnsm]?)?\b|open\b|wartet\b|warten\s+auf\b|waiting\b|awaiting\b|pending\b"
    r"|ausstehend\b|steht\s+(?:noch\s+)?aus\b|(?:im|in)\s+review\b|draft\b|entwurf\b|wip\b"
    r"|in\s+arbeit\b|in\s+progress\b)", re.I)
DONE = re.compile(
    r"\bgemergt\b|\bgemerged\b|\bmerged\b|\bzusammengeführt\b|\bgeschlossen\b|\bclosed\b|\berledigt\b|\bdone\b"
    r"|\bfertig\b|\blive\b|\bausgerollt\b|\bdeployed\b|\breleased\b|\bveröffentlicht\b|\babgeschlossen\b"
    r"|\bverworfen\b|\babgelehnt\b|\brejected\b|\bsuperseded\b|\bersetzt\b|✅|☑", re.I)
CLAUSE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ*_`\[(])|;\s+|\s+·\s+|\s*\|\s*")

TERMINAL = ("merged", "closed")
RECHECK_HOURS = {"open": 20, "draft": 20, "merged": 30 * 24, "closed": 30 * 24, "unknown": 7 * 24}


def key(repo: str, n: int) -> str:
    return "%s#%d" % (repo, int(n))


def repo_map_from_clones(roots, limit: int = 3000) -> dict:
    """{short name: 'owner/repo'} for the GitHub clones directly below `roots`. A name two different
    repositories share is left out: a wrong guess would report the state of the wrong pull request."""
    seen: dict = {}
    clash = set()

    def put(name: str, full: str) -> None:
        name = name.lower()
        if name in clash:
            return
        if seen.get(name, full) != full:
            clash.add(name)
            seen.pop(name, None)
            return
        seen[name] = full

    count = 0
    for root in roots or ():
        root = os.path.expanduser(str(root))
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            if count >= limit:
                break
            cfg = os.path.join(root, entry, ".git", "config")
            try:
                with open(cfg, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(20000)
            except OSError:
                continue
            count += 1
            m = CONFIG_URL.search(text)
            if not m:
                continue
            full = "%s/%s" % (m.group(1), m.group(2))
            put(m.group(2), full)
            put(entry, full)
    return seen


def _body_lines(text: str):
    """(index, line) for the lines that are prose: not the frontmatter, not fenced code."""
    lines = text.split("\n")
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    fence = False
    for k in range(i, len(lines)):
        if lines[k].lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            yield k, lines[k]


def _line_refs(line: str, repo_map: dict, default_repo: str) -> list:
    taken: list = []
    out: list = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(repo: str, n: str, a: int, b: int, how: str) -> None:
        taken.append((a, b))
        out.append({"repo": repo, "n": int(n), "start": a, "end": b, "how": how})

    for m in URL_RX.finditer(line):
        add("%s/%s" % (m.group(1), m.group(2)), m.group(3), m.start(), m.end(), "url")
    for m in FULL_RX.finditer(line):
        if free(m.start(), m.end()):
            add("%s/%s" % (m.group(1), m.group(2)), m.group(3), m.start(), m.end(), "full")
    for m in SHORT_RX.finditer(line):
        full = repo_map.get(m.group(1).lower())
        if full and free(m.start(), m.end()):
            add(full, m.group(2), m.start(), m.end(), "short")
    out.sort(key=lambda r: r["start"])
    for m in BARE_RX.finditer(line):
        if not free(m.start(), m.end()):
            continue
        before = [r for r in out if r["end"] <= m.start()]
        if before and LIST_GAP.search(line[before[-1]["end"]:m.start()]):
            add(before[-1]["repo"], m.group(1), m.start(), m.end(), "list")
            out.sort(key=lambda r: r["start"])
        elif default_repo and not before:
            add(default_repo, m.group(1), m.start(), m.end(), "default")
            out.sort(key=lambda r: r["start"])
    return out


def find_refs(text: str, repo_map: dict | None = None, default_repo: str = "") -> list:
    """Every reference in a note: [{repo, n, line, start, end, how}], `how` saying how sure the repository is:
    url, full (owner/repo#n), short (repo#n, resolved through repo_map), list (a bare #n that continues a list),
    default (a bare #n in a note that is about one repository)."""
    repo_map = repo_map or {}
    out = []
    for i, line in _body_lines(text):
        if "#" not in line and "github.com" not in line:
            continue
        for r in _line_refs(line, repo_map, default_repo):
            r["line"] = i
            out.append(r)
    return out


def _clause_span(line: str, start: int, end: int) -> tuple:
    """Where the sentence or list item around a reference begins and ends: what is said there is said about it."""
    a, b = 0, len(line)
    for m in CLAUSE_END.finditer(line):
        if m.end() <= start:
            a = m.end()
        elif m.start() >= end:
            b = m.start()
            break
    return a, b


def _clause(line: str, start: int, end: int) -> str:
    a, b = _clause_span(line, start, end)
    return line[a:b]


def _struck(line: str, start: int) -> bool:
    return line[:start].count("~~") % 2 == 1


def says_open(clause: str) -> bool:
    """The clause calls its subject unfinished and nowhere finished. 'not merged' counts as unfinished."""
    rest = NOT_DONE.sub(" ", clause)
    if DONE.search(rest):
        return False
    return bool(NOT_DONE.search(clause) or OPEN.search(rest))


def open_at(line: str, start: int, end: int) -> str:
    """Does the line call THIS reference open? -> 'open', 'unmerged' (it only says there was no merge) or ''.
    A word before the first reference of the clause speaks for all of them ("open PRs: api#1, api#2"); a word
    after one speaks for the reference just before it ("api#1 on branch x, card #10 in review" says nothing
    about api#1)."""
    a, b = _clause_span(line, start, end)
    clause = line[a:b]
    rest = NOT_DONE.sub(lambda m: " " * len(m.group(0)), clause)  # same length: the positions still match
    if DONE.search(rest):
        return ""
    marks = [(m.start(), "unmerged" if NOT_MERGED.fullmatch(m.group(0)) else "open") for m in NOT_DONE.finditer(clause)]
    marks += [(m.start(), "open") for m in OPEN.finditer(rest)]
    tokens = list(TOKEN_RX.finditer(clause))
    first = min([m.start() for m in tokens] + [start - a])
    ends = sorted({m.end() for m in tokens} | {end - a})
    said = set()
    for pos, what in marks:
        before = [e for e in ends if e <= pos]
        if pos < first or (before and before[-1] == end - a):
            said.add(what)
    return "open" if "open" in said else ("unmerged" if said else "")


def stale_lines(text: str, cache: dict, repo_map: dict | None = None, default_repo: str = "",
                weak: bool = False) -> list:
    """Lines that call a merged or closed pull request or issue open.
    -> [{ref, repo, n, line_no, line, state, at, title, kind, how}]. A reference whose repository was only
    guessed from the note (`default`) is reported with weak=True only."""
    lines = text.split("\n")
    out, seen = [], set()
    for r in find_refs(text, repo_map, default_repo):
        if r["how"] == "default" and not weak:
            continue
        k = key(r["repo"], r["n"])
        st = cache.get(k) or {}
        if st.get("state") not in TERMINAL or (k, r["line"]) in seen:
            continue
        line = lines[r["line"]]
        if re.match(r"\s*[-*]\s+\[[xX]\]", line) or _struck(line, r["start"]):
            continue
        said = open_at(line, r["start"], r["end"])
        if not said or (said == "unmerged" and st["state"] == "closed"):
            continue  # "not merged" about a pull request that was closed without a merge is still right
        seen.add((k, r["line"]))
        out.append({"ref": k, "repo": r["repo"], "n": r["n"], "line_no": r["line"] + 1, "line": line.strip(),
                    "state": st["state"], "at": st.get("at", ""), "title": st.get("title", ""),
                    "kind": st.get("kind", "pr"), "how": r["how"]})
    return out


# --- GitHub -----------------------------------------------------------------------------------

def gh_available() -> bool:
    return bool(shutil.which("gh"))


def _now(now=None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def due(entry, now=None) -> bool:
    """Ask again? An open item changes any day, a merged one almost never, an unknown one may appear."""
    if not isinstance(entry, dict) or not entry.get("checked"):
        return True
    try:
        checked = _dt.datetime.strptime(str(entry["checked"])[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    hours = RECHECK_HOURS.get(entry.get("state"), 20)
    return (_now(now) - checked).total_seconds() > hours * 3600


def _query(group: list) -> str:
    """One GraphQL document for [(alias, owner, name, [numbers])]."""
    parts = []
    for alias, owner, name, numbers in group:
        items = " ".join(
            "n%d: issueOrPullRequest(number: %d) { __typename ... on PullRequest { state isDraft mergedAt closedAt"
            " title } ... on Issue { state closedAt title } }" % (n, n) for n in numbers)
        parts.append("%s: repository(owner: %s, name: %s) { %s }" % (alias, json.dumps(owner), json.dumps(name), items))
    return "query { %s }" % " ".join(parts)


def _run_gh(query: str):
    """-> (returncode, stdout, stderr). Never waits for a prompt: this runs at night."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    try:
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query], capture_output=True, text=True,
                           timeout=90, env=env)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "gh not found"
    except subprocess.TimeoutExpired:
        return 124, "", "gh timed out"
    except OSError as e:
        return 126, "", str(e)


def _state(node) -> dict:
    if not isinstance(node, dict):
        return {"state": "unknown"}
    pr = node.get("__typename") == "PullRequest"
    raw = str(node.get("state") or "").upper()
    if raw == "MERGED":
        state = "merged"
    elif raw == "CLOSED":
        state = "closed"
    elif raw == "OPEN":
        state = "draft" if node.get("isDraft") else "open"
    else:
        state = "unknown"
    at = str(node.get("mergedAt") or node.get("closedAt") or "")[:10]
    return {"state": state, "kind": "pr" if pr else "issue", "title": " ".join(str(node.get("title") or "").split())[:140],
            "at": at if state in TERMINAL else ""}


def fetch_states(keys, cache: dict, now=None, run=None, batch: int = 60, max_calls: int = 20) -> dict:
    """Bring `cache` up to date for the references that are due. -> {asked, calls, errors}.
    `cache` maps 'owner/repo#n' to {state, kind, title, at, checked}; state is open, draft, merged, closed
    or unknown (no such item, or no access). `run` replaces the gh call in tests."""
    run = run or _run_gh
    stamp = _now(now).strftime("%Y-%m-%dT%H:%M:%S")
    todo: dict = {}
    for k in sorted(set(keys)):
        if not due(cache.get(k), now):
            continue
        repo, _, num = k.rpartition("#")
        if "/" not in repo or not num.isdigit():
            continue
        todo.setdefault(repo, []).append(int(num))
    rep = {"asked": sum(len(v) for v in todo.values()), "calls": 0, "errors": []}
    groups, cur, size = [], [], 0
    for i, (repo, numbers) in enumerate(sorted(todo.items())):
        owner, name = repo.split("/", 1)
        for j in range(0, len(numbers), batch):
            chunk = numbers[j:j + batch]
            if cur and size + len(chunk) > batch:
                groups.append(cur)
                cur, size = [], 0
            cur.append(("r%d_%d" % (i, j), owner, name, chunk))
            size += len(chunk)
    if cur:
        groups.append(cur)
    for group in groups:
        if rep["calls"] >= max_calls:
            rep["errors"].append("stopped after %d calls, the rest waits for the next run" % max_calls)
            break
        rc, out, err = run(_query(group))
        rep["calls"] += 1
        try:
            data = json.loads(out).get("data")
        except (ValueError, AttributeError):
            data = None
        if not isinstance(data, dict):  # no login, no network, rate limit: nothing is known, nothing is changed
            rep["errors"].append(" ".join((err or out or "gh failed").split())[:200])
            break
        for alias, owner, name, numbers in group:
            repo_node = data.get(alias)
            for n in numbers:
                node = repo_node.get("n%d" % n) if isinstance(repo_node, dict) else None
                entry = _state(node)
                entry["checked"] = stamp
                old = cache.get(key("%s/%s" % (owner, name), n)) or {}
                if entry["state"] == "unknown" and old.get("state") in TERMINAL:
                    entry = dict(old, checked=stamp)  # a repo that vanished does not un-merge what was merged
                cache[key("%s/%s" % (owner, name), n)] = entry
    return rep


# --- words ------------------------------------------------------------------------------------

_WORDS = {
    "en": {"pr": "pull request", "issue": "issue", "merged": "was merged", "closed": "was closed", "on": " on ",
           "item": "{kind} {ref}{title} {state}{at}",
           "claim": "GitHub: {items}. The note {note} still calls {it} open: \"{line}\"",
           "it": ("it", "them"),
           "finding": "{ref} {state}{at} (GitHub), but line {line_no} still calls it open"},
    "de": {"pr": "Pull Request", "issue": "Issue", "merged": "ist gemergt", "closed": "ist geschlossen", "on": " seit ",
           "item": "{kind} {ref}{title} {state}{at}",
           "claim": "GitHub: {items}. Die Notiz {note} führt {it} noch als offen: „{line}“",
           "it": ("das", "sie"),
           "finding": "{ref} {state}{at} (GitHub), Zeile {line_no} führt das noch als offen"},
}


def _date(iso: str, lang: str) -> str:
    if lang == "de" and re.match(r"^\d{4}-\d{2}-\d{2}$", iso or ""):
        return "%s.%s.%s" % (iso[8:10], iso[5:7], iso[:4])
    return iso or ""


def _parts(f: dict, lang: str) -> dict:
    w = _WORDS.get(lang, _WORDS["en"])
    return {"kind": w.get(f.get("kind", "pr"), w["pr"]), "ref": f["ref"],
            "title": " (%s)" % f["title"] if f.get("title") else "", "state": w.get(f["state"], f["state"]),
            "at": (w["on"] + _date(f.get("at", ""), lang)) if f.get("at") else "", "line_no": f.get("line_no", 0)}


def claim(found, note: str, lang: str = "en") -> str:
    """One sentence that makes sense on its own, for the inbox: what GitHub says and what the note says.
    `found` is one finding or all findings of one line (they share the line, so they share the claim)."""
    fs = [found] if isinstance(found, dict) else list(found)
    w = _WORDS.get(lang, _WORDS["en"])
    line = fs[0].get("line", "")
    return w["claim"].format(items="; ".join(w["item"].format(**_parts(f, lang)) for f in fs), note=note,
                             it=w["it"][len(fs) > 1], line=line if len(line) <= 240 else line[:239] + "…")


def finding(f: dict, lang: str = "en") -> str:
    """The short form for a lint line."""
    return _WORDS.get(lang, _WORDS["en"])["finding"].format(**_parts(f, lang))
