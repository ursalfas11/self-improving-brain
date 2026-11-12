"""Self-care: what `brain lint` lists as upkeep is fixed at night instead of waiting for you.

  description   a note without one gets its first sentence as description (recall and the index rely on it)
  orphan        a note that links to nothing is linked to the hub it fits, and the hub lists it
  trigger       a reflex rule without any trigger gets the rule's own words
  shorten       a rule longer than 240 characters is shortened by the LLM; names, commands and numbers must survive
  duplicate     the LLM reads two notes that look alike: different → remembered and never reported again,
                the same → merged into one note, and the other one points to it

Every fix is checked and rolled back when a check fails (no secret, no number, link, code or rule lost, no new
MUST finding). Every fix is one journal line: `brain review` shows it, `brain undo` takes it back, and what you
took back is not fixed again. A note with changes you have not committed is left alone that night.

Not touched, because only you know: a stale project, a rule that waits for `brain trust`, two notes with one name.
"""
from __future__ import annotations

import re

from . import journal, learn, llm, trust
from .config import Vault
from .lint import duplicate_pairs, lint_note
from .notes import Library, Note, atomic_write, dump_frontmatter, parse_frontmatter, set_field
from .search import Index
from .secrets import scan
from .util import days_since, load_json, save_json, short, terms, today

MERGE_MAX = 6000     # characters per note: above that a merge is not safe to check, the pair stays as it is
TRIES_MAX = 2        # failed attempts per finding, then it rests
REST_DAYS = 30
IDENT = re.compile(r"\[\[[^\]|#]+|`[^`\n]+`|https?://[^\s)>\]]+|#\d+|(?<![\w.,])\d[\d.,:]*\d|\b\d{2,}\b")
FOOTER = re.compile(r"^\s*(Part of|Linked from|Merged into) \[\[[^\]]+\]\]")

SHORTEN = llm.MARKER + """
Shorten this rule for a coding agent to ONE imperative sentence of at most 200 characters.
Keep every concrete name, command, path and number. Drop explanations and examples. Do not change what it demands.
Return JSON: {"rule": "<the shorter rule>"}

Rule: @RULE@
"""

MERGE = llm.MARKER + """
Two notes of a personal Markdown wiki look like the same topic. Read both.
- different: they are about different things (another project, system, time or question). Nothing changes.
- same: they say the same thing. Write ONE body for the note to keep: every fact, number, link and code block of
  both notes exactly once, in the style of the kept note, nothing invented, rule callouts (> [!lesson] ...) unchanged.
Return JSON: {"verdict": "same"|"different", "keep": "<name of the note to keep>",
 "body": "<for same: the full merged Markdown body, starting with the # title line>", "reason": "<one short sentence>"}

=== NOTE @A@ ===
@ABODY@

=== NOTE @B@ ===
@BBODY@
"""


def _idents(text: str) -> set[str]:
    """What must not get lost: numbers from two digits, #-numbers, links, code spans, URLs."""
    out = set()
    for m in IDENT.findall(text or ""):
        k = re.sub(r"\s+", " ", m).strip().strip(".,:;").lower()
        if k.startswith("[["):
            k = "[[" + k[2:].split("/")[-1].strip()
        if len(k) >= 2:
            out.add(k)
    return out


def _pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def _vetoed(v: Vault) -> set[tuple]:
    """What you took back: (what, note or rule, other note). The brain does not fix it a second time."""
    return {(e.get("what") or e.get("kind"), *_pair(str(e.get("lesson") or e.get("note") or ""), str(e.get("other") or "")))
            for e in journal.entries(v) if e.get("kind") in ("care", "merge") and e.get("status") == "undone"}


def distinct(v: Vault) -> set[tuple[str, str]]:
    """Pairs of notes that look alike and are not: judged different at night, or merged and taken back by you."""
    out = set()
    for e in journal.entries(v):
        if e.get("kind") == "care" and e.get("what") == "distinct" and e.get("status") == "done":
            out.add(_pair(str(e.get("note")), str(e.get("other"))))
        elif e.get("kind") == "merge" and e.get("status") == "undone":
            out.add(_pair(str(e.get("note")), str(e.get("other"))))
    return out


def dirty_paths(v: Vault) -> set[str]:
    """Notes you are working on (changes not committed): the night keeps its hands off them. One git call."""
    if not (v.root / ".git").exists():
        return set()
    from .distill import _git
    out = set()
    for line in _git(v, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        p = line[3:].split(" -> ")[-1].strip().strip('"')
        if p:
            out.add(p)
    return out


def _commit(v: Vault, rels: list[str], msg: str) -> None:
    if (v.root / ".git").exists() and (v.get("git_owned") or v.get("git_sync")):
        from . import nightly
        from .distill import _git
        _git(v, "add", "--", *rels)
        _git(v, *nightly.identity(v.root), "commit", "-q", "-m", f"care: {msg}", "--", *rels)


def _must(v: Vault, names: list[str]) -> set[str]:
    lib = Library(v.root)
    return {f"{n}: {f.msg}" for n in names if lib.find(n) for f in lint_note(lib.find(n), lib) if f.level == "MUST"}


# --- without the LLM ------------------------------------------------------------------------

def first_sentence(n: Note) -> str:
    fence = False
    for line in n.body.split("\n"):
        t = line.strip()
        if t.startswith("```"):
            fence = not fence
            continue
        if fence or not t or t.startswith(("#", ">", "|", "---", "<!--")) or FOOTER.match(t):
            continue
        t = re.sub(r"^([-*+]|\d+\.)\s+", "", t)
        t = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", t)
        t = re.sub(r"[*_`]", "", t).strip()
        if len(terms(t)) < 3:
            continue
        m = re.match(r"(.{20,160}?[.!?])(\s|$)", t)
        return (m.group(1) if m else short(t, 160)).strip()
    return ""


def _describe(v: Vault, n: Note) -> dict | None:
    desc = first_sentence(n)
    if not desc or scan(desc):
        return None
    raw = n.path.read_text(encoding="utf-8")
    new = set_field(raw, "description", desc)
    block = ""
    if new == raw:  # no frontmatter yet
        block = dump_frontmatter({"description": desc})
        new = block + ("" if raw.startswith("\n") else "\n") + raw
    atomic_write(n.path, new)
    if not Library(v.root).find(n.name) or not Library(v.root).find(n.name).description:
        atomic_write(n.path, raw)
        return None
    return journal.log(v, "care", what="description", note=n.name, path=n.rel, after=desc, block=block,
                       why="the note had no description; this is its first sentence")


def hub_for(n: Note, lib: Library) -> Note | None:
    hubs = [h for h in lib.notes.values() if h.is_hub]
    if len(hubs) <= 1:
        return hubs[0] if hubs else None
    for h in Index(lib).score(f"{n.title} {n.description} " + " ".join(terms(n.body)[:60]), include_hubs=True):
        if h.note.is_hub:
            return h.note
    here = [h for h in hubs if h.path.parent == n.path.parent]
    return here[0] if len(here) == 1 else None  # no evidence for any hub: not guessed


def _adopt_orphan(v: Vault, n: Note, lib: Library, dirty: set[str] = frozenset()) -> dict | None:
    from .distill import _append
    hub = hub_for(n, lib)
    if hub is None or hub.rel in dirty:
        return None
    raw, hub_raw = n.path.read_text(encoding="utf-8"), hub.path.read_text(encoding="utf-8")
    text = f"Part of [[{hub.name}]]."
    atomic_write(n.path, set_field(raw.rstrip() + "\n\n" + text + "\n", "updated", today()))
    line = ""
    if not any(lib.resolve(l) is n for l in hub.links):
        line = f"- [[{n.name}]] — {short(n.description or n.title, 100)}"
        _append(hub.path, "", line)
    if _must(v, [n.name, hub.name]) - _must_before(lib, [n, hub]):
        atomic_write(n.path, raw)
        atomic_write(hub.path, hub_raw)
        return None
    return journal.log(v, "care", what="orphan", note=n.name, path=n.rel, text=text, hub=hub.rel, hub_line=line,
                       why=f"the note linked to nothing; {hub.name} is the hub it fits")


def _must_before(lib: Library, notes: list[Note]) -> set[str]:
    return {f"{n.name}: {f.msg}" for n in notes for f in lint_note(n, lib) if f.level == "MUST"}


def _trigger(v: Vault, l) -> dict | None:
    words = [w for w in dict.fromkeys(terms(l.rule)) if w not in learn.FILLER and len(w) >= 4][:4]
    if len(words) < 2:
        return None
    learn.set_lesson_field(v, l.id, "words", ", ".join(words), by="care")
    return journal.log(v, "care", what="trigger", lesson=l.id, note=l.note, field="words", before="",
                       after=", ".join(words), why=f"the {l.mechanism} rule had no trigger, so it never fired")


# --- with the LLM ---------------------------------------------------------------------------

def _shorten(v: Vault, l) -> dict | None:
    d = llm.ask_json(v, SHORTEN.replace("@RULE@", l.rule))
    new = " ".join(str(d.get("rule") or "").split()) if isinstance(d, dict) else ""
    if not 20 <= len(new) <= 240 or learn._suspicious(new) or scan(new):
        raise ValueError("the shorter rule was empty, still too long, or not safe")
    lost = _idents(l.rule) - _idents(new)
    if lost:
        raise ValueError("the shorter rule lost " + ", ".join(sorted(lost)[:4]))
    learn.set_lesson_field(v, l.id, "rule", new, by="care")
    return journal.log(v, "care", what="shorten", lesson=l.id, note=l.note, field="rule", before=l.rule, after=new,
                       why="the rule was longer than 240 characters; every session pays for its length")


def _body_of(raw: str) -> tuple[str, str]:
    _, body = parse_frontmatter(raw)
    return raw[: len(raw) - len(body)], body


def _merge(v: Vault, a: Note, b: Note) -> dict | None:
    """Returns the journal entry of a merge or of the verdict "different". Raises ValueError when the answer
    could not be used; the files are then as they were."""
    if max(len(a.body), len(b.body)) > MERGE_MAX or (a.lessons and b.lessons):
        raise ValueError("too large to merge safely, or both notes carry rules")
    d = llm.ask_json(v, MERGE.replace("@A@", a.name).replace("@ABODY@", a.body.strip())
                     .replace("@B@", b.name).replace("@BBODY@", b.body.strip()))
    if not isinstance(d, dict) or d.get("verdict") not in ("same", "different"):
        raise ValueError("no verdict")
    why = short(str(d.get("reason") or ""), 200)
    if d["verdict"] == "different":
        return journal.log(v, "care", what="distinct", note=a.name, other=b.name, why=why or "about different things")
    keep, other = (a, b) if str(d.get("keep") or "") == a.name else (b, a) if str(d.get("keep") or "") == b.name else (None, None)
    if keep is None:
        raise ValueError("the note to keep is neither of the two")
    if other.lessons:  # rules never move: the note that carries them is the one that stays
        keep, other = other, keep
    body = str(d.get("body") or "").strip()
    keep_raw, other_raw = keep.path.read_text(encoding="utf-8"), other.path.read_text(encoding="utf-8")
    if len(body) < 0.6 * max(len(keep.body.strip()), len(other.body.strip())):
        raise ValueError("the merged note is much shorter than the longer of the two")
    if len(scan(body)) > len(scan(keep.body)) + len(scan(other.body)):
        raise ValueError("the merged note looks like it holds a secret")
    own = {"[[" + keep.name.lower(), "[[" + other.name.lower()}
    lost = (_idents(keep.body) | _idents(other.body)) - _idents(body) - own
    if lost:
        raise ValueError("the merged note lost " + ", ".join(sorted(lost)[:5]))
    said = set(terms(body))
    for n in (keep, other):  # prose has no number to check: most of each note's words must still be there
        have = set(terms(n.body))
        if have and len(have & said) / len(have) < 0.8:
            raise ValueError(f"the merged note dropped too much of {n.name}")
    must_before = _must_before(Library(v.root), [keep, other])
    head, _ = _body_of(keep_raw)
    ohead, _ = _body_of(other_raw)
    atomic_write(keep.path, set_field(head + ("" if head.endswith("\n\n") or not head else "\n") + body + "\n",
                                      "updated", today()))
    stub = set_field(set_field(ohead + ("" if ohead.endswith("\n\n") or not ohead else "\n")
                               + f"# {other.title}\n\nMerged into [[{keep.name}]] on {today()}.\n",
                               "description", f"Merged into {keep.name}."), "updated", today())
    atomic_write(other.path, stub)
    after = Library(v.root).find(keep.name)
    ok = (after is not None and [l.as_callout() for l in after.lessons] == [l.as_callout() for l in keep.lessons]
          and not _must(v, [keep.name, other.name]) - must_before)
    if not ok:
        atomic_write(keep.path, keep_raw)
        atomic_write(other.path, other_raw)
        raise ValueError("the merged note changed a rule or broke a link")
    return journal.log(v, "merge", note=keep.name, other=other.name, path=keep.rel, other_path=other.rel,
                       before=keep_raw, other_before=other_raw,
                       after=journal.sha(keep.path.read_text(encoding="utf-8")),
                       other_after=journal.sha(other.path.read_text(encoding="utf-8")),
                       why=why or "both notes said the same")


# --- the nightly step -----------------------------------------------------------------------

def run(v: Vault, think: bool = False) -> dict:
    """Fix what can be fixed. Returns {"fixed": [...], "distinct": n, "resting": n} for the nightly report."""
    out: dict = {"fixed": [], "distinct": 0, "resting": 0}
    veto = _vetoed(v)
    dirty = dirty_paths(v)
    state_path = v.path("state", "care.json")
    state = load_json(state_path, {})
    state = state if isinstance(state, dict) else {}

    def rests(key: str) -> bool:
        m = state.get(key) or {}
        return int(m.get("n") or 0) >= TRIES_MAX and (days_since(str(m.get("t") or "")) or 0) < REST_DAYS

    def done(entry: dict | None, label: str, rels: list[str]) -> None:
        if entry:
            out["fixed"].append(label)
            _commit(v, rels, label)

    lib = Library(v.root)
    for n in list(lib.notes.values()):
        if n.is_hub or n.rel in dirty:
            continue
        try:
            if not n.description and ("description", *_pair(n.name, "")) not in veto:
                done(_describe(v, n), f"description {n.name}", [n.rel])
            if not n.links and ("orphan", *_pair(n.name, "")) not in veto:
                hub = hub_for(n, lib)
                done(_adopt_orphan(v, Library(v.root).find(n.name) or n, Library(v.root), dirty), f"orphan {n.name}",
                     [n.rel] + ([hub.rel] if hub else []))
        except (OSError, ValueError, KeyError, UnicodeError) as e:  # one odd note must not stop the others
            out.setdefault("errors", []).append(f"{n.name}: {type(e).__name__}: {e}"[:160])

    lib = Library(v.root)
    by_name = {n.name: n for n in lib.notes.values()}
    active = trust.effective(v, lib.lessons(), record_seen=False)  # a rule that waits for `brain trust` is not touched
    for l in active:
        note = by_name.get(l.note)
        if note is None or note.rel in dirty:
            continue
        try:
            if (l.mechanism in ("reflex", "skill") and not (l.words or l.tools or l.commands or l.paths)
                    and ("trigger", *_pair(l.id, "")) not in veto):
                done(_trigger(v, l), f"trigger {l.id}", [note.rel])
        except (OSError, ValueError, KeyError, UnicodeError) as e:
            out.setdefault("errors", []).append(f"{l.id}: {type(e).__name__}: {e}"[:160])

    if think:
        todo: list[tuple[str, object]] = []
        for l in active:
            if len(l.rule) > 240 and ("shorten", *_pair(l.id, "")) not in veto:
                todo.append((f"shorten {l.id}", l))
        known = distinct(v)
        for a, b, _j in duplicate_pairs(lib):
            if _pair(a, b) not in known and ("merge", *_pair(a, b)) not in veto:
                todo.append((f"duplicate {a} {b}", (a, b)))
        left = int(v.get("care_max"))
        for key, what in todo:
            if rests(key):
                out["resting"] += 1
                continue
            if left <= 0:
                break
            left -= 1
            try:
                if isinstance(what, tuple):
                    a, b = lib.notes[what[0]], lib.notes[what[1]]
                    if a.rel in dirty or b.rel in dirty:
                        continue
                    e = _merge(v, a, b)
                    if e and e.get("kind") == "merge":
                        done(e, f"merged {e['other']} into {e['note']}", [e["path"], e["other_path"]])
                        lib = Library(v.root)
                    else:
                        out["distinct"] += 1
                else:
                    note = by_name.get(what.note)  # type: ignore[attr-defined]
                    if note is None or note.rel in dirty:
                        continue
                    done(_shorten(v, what), key, [note.rel])
                state.pop(key, None)
            except llm.NoLLM:
                break  # budget used up or the CLI is gone: the rest waits for the next night
            except (ValueError, KeyError, OSError, UnicodeError) as e:
                m = state.setdefault(key, {"n": 0})
                m.update(n=int(m.get("n") or 0) + 1, t=today(), why=short(str(e), 160))
        save_json(state_path, state)
    return out


def undo(v: Vault, e: dict) -> str:
    """Take one fix back (called by journal.undo). Raises ValueError when the note changed since."""
    what = e.get("what") or e.get("kind")
    if e.get("kind") == "merge":
        p, o = v.root / e["path"], v.root / e["other_path"]
        if (not p.exists() or not o.exists() or journal.sha(p.read_text(encoding="utf-8")) != e.get("after")
                or journal.sha(o.read_text(encoding="utf-8")) != e.get("other_after")):
            raise ValueError(f"{e['path']} or {e['other_path']} changed since the merge — edit them by hand")
        atomic_write(p, e["before"])
        atomic_write(o, e["other_before"])
        return f"{e['note']} and {e['other']} are two notes again, and are not merged a second time"
    if what in ("trigger", "shorten"):
        learn.set_lesson_field(v, e["lesson"], e["field"], e.get("before", ""), by="undo")
        return f"{e['lesson']} has its old {e['field']} again"
    if what == "distinct":
        return f"{e.get('note')} and {e.get('other')} are compared again next night"
    p = v.root / e["path"]
    cur = p.read_text(encoding="utf-8") if p.exists() else None
    if cur is None:
        raise ValueError(f"{e['path']} is gone")
    if what == "description":
        if e.get("block") and cur.startswith(e["block"]):
            atomic_write(p, cur[len(e["block"]):].lstrip("\n"))
        else:
            meta, _ = parse_frontmatter(cur)
            if str(meta.get("description") or "") != e.get("after"):
                raise ValueError(f"the description of {e['path']} changed since — edit it by hand")
            atomic_write(p, re.sub(r"(?m)^description:.*\n", "", cur, count=1))
        return f"{e.get('note')} has no description again, and the brain will not write one"
    if what == "orphan":
        atomic_write(p, journal._drop_block(cur, e["text"]))
        hub = v.root / e["hub"] if e.get("hub") else None
        if hub is not None and hub.exists() and e.get("hub_line"):
            h = hub.read_text(encoding="utf-8")
            if e["hub_line"] in h:
                atomic_write(hub, journal._drop_block(h, e["hub_line"]))
        return f"{e.get('note')} links to nothing again, and the brain will not link it"
    raise ValueError(f"{e.get('id')} ({what}) cannot be undone")
