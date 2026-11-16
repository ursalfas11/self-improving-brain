"""Distill: turn inbox items into wiki edits (Karpathy's LLM-wiki pattern, locally).

Two ways, same rules:
  * `brain distill --auto`   (the nightly run) uses `llm_cmd` to decide per item: APPEND to the note that owns
                             the topic, UPDATE an outdated statement there (the newer state wins; the old one
                             stays in git history and in the journal), a NEW note, or SKIP. Every patch is
                             secret-scanned and linted, a failing patch is rolled back, every applied one is
                             journaled and can be undone with `brain undo`.
  * `brain distill`          prints a work packet for your agent instead, for setups without an LLM CLI.

No item waits for you. A claim that could not be filed is tried again the next night with the reason in the
prompt; after TRIES_MAX nights it is dropped, which is one line in the journal (`brain undo` puts it back).
An answer the LLM garbled is not the claim's fault: the item only moves to the back of the queue.
"""
from __future__ import annotations

import re
import subprocess

from . import events, inbox, journal, llm, nightly
from .config import Vault
from .lint import lint_note
from .notes import Library, atomic_write, dump_frontmatter, parse_frontmatter, set_field
from .search import Index
from .secrets import scan
from .util import short, slugify, today

# what a fact may never touch: the frontmatter, code, and the rules (a fact from the agent's words must not
# rewrite one), and text that would turn into a rule
LESSON_LIKE = re.compile(r"\[!lesson\]", re.I)
TRIES_MAX = 3


def _protected(text: str) -> list[tuple[int, int]]:
    """Character spans of the frontmatter, fenced code and lesson callouts."""
    spans, pos, fence, callout = [], 0, None, None
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            spans.append((0, end + 4))
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if line.startswith("```"):
            if fence is None:
                fence = start
            else:
                spans.append((fence, pos))
                fence = None
            continue
        if fence is not None:
            continue
        if callout is None and LESSON_LIKE.search(line) and line.lstrip().startswith(">"):
            callout = start
        elif callout is not None and not line.lstrip().startswith(">"):
            spans.append((callout, start))
            callout = None
    if fence is not None:
        spans.append((fence, pos))
    if callout is not None:
        spans.append((callout, pos))
    return spans


FOLDER_FOR = {"fact": "20-areas", "decision": "20-areas", "pitfall": "20-areas", "preference": "20-areas",
              "status": "10-projects", "todo": "10-projects", "lesson": "20-areas"}


def candidates(lib: Library, claim: str, k: int = 3):
    return Index(lib).score(claim, include_hubs=False)[:k]


def packet(v: Vault) -> str:
    items = inbox.items(v)
    if not items:
        return "Inbox is empty."
    lib = Library(v.root)
    out = [f"{len(items)} inbox items. For each: put the claim into the ONE note that owns the topic"
           " (update a stale statement instead of adding a second one), link it to its hub, then run"
           " `brain inbox done <id>`. The claim is newer than the wiki: when it contradicts existing text,"
           " replace that text.", ""]
    for it in items:
        out.append(f"[{it['id']}] {it['type']}: {it['claim']}")
        if it.get("quote"):
            out.append(f"    quote: {short(it['quote'], 160)}")
        for h in candidates(lib, it["claim"]):
            out.append(f"    → {h.note.rel}  (score {h.score}{', ' + h.section.title if h.section else ''})")
    return "\n".join(out)


DECIDE = llm.MARKER + """
You maintain a personal Markdown wiki. Decide where a new claim goes.
Rules: one truth per fact; keep the existing style; never invent facts. The claim is newer than the wiki:
when it contradicts existing text, replace that text.
Return JSON: {"action": "append"|"update"|"new"|"skip",
 "note": "<existing note name for append/update>", "section": "<existing H2 title or empty>",
 "old": "<for update: the outdated text, copied exactly from the note, one line or sentence>",
 "text": "<markdown to add, or the replacement for old: one bullet or a short paragraph>",
 "title": "<for new: note title>", "description": "<for new: one line>",
 "hub": "<for new: best hub note name from the list, or empty>",
 "reason": "<one short sentence>"}
skip = already covered, or not worth keeping.
@HINT@
Claim (@TYPE@): @CLAIM@
Quote: @QUOTE@

Candidate notes:
@CANDS@

Hubs: @HUBS@
"""


FOOTER = re.compile(r"^\s*(Part of|Linked from) \[\[[^\]]+\]\]")


def _append(path, section: str, text: str) -> None:
    raw = set_field(path.read_text(encoding="utf-8"), "updated", today())
    _, body = parse_frontmatter(raw)
    head = raw[: len(raw) - len(body)]
    lines = body.split("\n")  # splitlines() would also break at U+2028, form feeds and other rare characters
    heads, fence = [], False
    for i, l in enumerate(lines):
        if l.startswith("```"):
            fence = not fence
        elif not fence and l.startswith("## "):
            heads.append(i)
    insert_at = len(lines)
    for k, i in enumerate(heads):
        if section and lines[i][3:].strip().lower() == section.lower():
            j = heads[k + 1] if k + 1 < len(heads) else len(lines)
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            insert_at = j
            break
    while insert_at > 0 and insert_at == len(lines) and not lines[-1].strip():
        lines.pop()
        insert_at = len(lines)
    if insert_at == len(lines) and insert_at and FOOTER.match(lines[-1]):  # "Part of [[hub]]." stays the last line
        insert_at -= 1
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
    block = text.rstrip().split("\n")
    prev = lines[insert_at - 1] if insert_at else ""
    bullet = block and block[0].lstrip().startswith(("- ", "* "))
    if prev.strip() and not (bullet and prev.lstrip().startswith(("- ", "* "))):
        block = [""] + block
    if insert_at < len(lines) and lines[insert_at].strip():
        block = block + [""]
    lines[insert_at:insert_at] = block
    atomic_write(path, head + "\n".join(lines).rstrip() + "\n")


def _git(v: Vault, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(v.root), *args], capture_output=True, text=True)


def _about(n, quote: str) -> str:
    """The note a claim names (a check found something in it), with the lines around the quoted one."""
    lines = n.body.split("\n")
    at = next((i for i, l in enumerate(lines) if quote and quote.strip() in l), None)
    part = lines[max(0, at - 8): at + 9] if at is not None else lines[:40]
    return (f"### {n.name} ({n.rel}) — the claim is about this note\n{n.description}\n"
            + "\n".join(part)[:2500])


def auto(v: Vault, limit: int = 20) -> list[str]:
    if not llm.available(v):
        raise llm.NoLLM("distill --auto needs llm_cmd (see `brain config`)")
    report = []
    lib = Library(v.root)
    hubs = ", ".join(n.name for n in lib.notes.values() if n.is_hub) or "(none)"
    # an item that failed before must not block the ones behind it: fresh ones first
    queue = sorted(inbox.items(v, mine=True), key=lambda i: int(i.get("tries") or 0) + int(i.get("stumbles") or 0))
    for it in queue[:limit]:
        cands = candidates(lib, it["claim"])
        about = lib.find(str(it["note"])) if it.get("note") else None
        ctext = "\n\n".join(([_about(about, it.get("quote", ""))] if about else [])
                            + [f"### {h.note.name} ({h.note.rel})\n{h.note.description}\n"
                               f"{(h.section.body if h.section else '')[:1500]}"
                               for h in cands if about is None or h.note.name != about.name]) or "(none)"
        hint = str(it.get("hint") or it.get("conflict") or "")
        prompt = (DECIDE.replace("@HINT@", f"\nAn earlier attempt to file this claim failed: {hint}. Decide so that "
                                           "this cannot happen again; when it cannot be filed safely, skip it.\n"
                                 if hint else "")
                  .replace("@TYPE@", it["type"]).replace("@CLAIM@", it["claim"])
                  .replace("@QUOTE@", it.get("quote", "")).replace("@HUBS@", hubs).replace("@CANDS@", ctext))
        try:
            d = llm.ask_json(v, prompt)
        except llm.NoLLM as e:
            report.append(f"stopped: {e}")
            break  # budget used up or the CLI is gone: the rest waits for the next night
        except ValueError as e:
            _stumbled(v, it["id"])
            report.append(f"{it['id']}: llm error {e}")
            continue
        if not isinstance(d, dict):
            _stumbled(v, it["id"])
            report.append(f"{it['id']}: llm error: expected a JSON object")
            continue
        act = str(d.get("action", "")).lower()
        if act == "skip":
            inbox.close(v, it["id"], "skipped", d.get("reason", ""))
            report.append(f"{it['id']}: skip — {d.get('reason', '')}")
            continue
        if act not in ("append", "update", "new"):
            report.append(f"{it['id']}: {_failed(v, it, str(d.get('reason') or 'no usable decision'))}")
            continue
        text = str(d.get("text", "")).strip()
        if not text or scan(text) or LESSON_LIKE.search(text):
            report.append(f"{it['id']}: refused (empty, secret-like or rule-like text), "
                          + _failed(v, it, "the text was empty, looked like a secret, or like a rule"))
            continue
        entry: dict = {"action": act, "claim": it["claim"], "text": text, "why": d.get("reason", "")}
        if act in ("append", "update"):
            target = lib.find(str(d.get("note", "")))
            if target is None:
                report.append(f"{it['id']}: " + _failed(v, it, f"note {d.get('note')!r} does not exist; use a name "
                                                                 "from the candidate notes, or action new"))
                continue
            must_before = {f.msg for f in lint_note(target, lib) if f.level == "MUST"}
            path, before, hub_before = target.path, target.path.read_text(encoding="utf-8"), None
            old = str(d.get("old") or "").strip()
            if act == "update":
                at = before.find(old) if old else -1
                if at < 0 or before.count(old) != 1 or "\n" in old or "\n" in text:
                    report.append(f"{it['id']}: update target not found, " + _failed(
                        v, it, "the outdated text to replace was not one line found exactly once in the note; "
                               "copy it exactly, or append instead"))
                    continue
                if any(a < at + len(old) and at < b for a, b in _protected(before)):
                    report.append(f"{it['id']}: would change a rule, " + _failed(
                        v, it, "the text to replace is part of a rule, the frontmatter or code, which a fact never changes"))
                    continue
                atomic_write(path, set_field(before.replace(old, text, 1), "updated", today()))
                entry["old"] = old
            else:
                _append(path, str(d.get("section", "")), text)
        else:
            title = str(d.get("title") or it["claim"][:60])
            base = name = slugify(title)
            k = 2
            while name in lib.notes or name.lower() in lib.by_stem:  # never a second note with a name you use
                name, k = f"{base}-{k}", k + 1
            folder = v.root / FOLDER_FOR.get(it["type"], "20-areas")
            folder.mkdir(parents=True, exist_ok=True)
            path, before, hub_before, must_before = folder / f"{name}.md", None, None, set()
            if path.exists():
                report.append(f"{it['id']}: {path.name} exists, " + _failed(v, it, f"{path.name} exists"))
                continue
            hub_note = lib.find(str(d.get("hub") or "")) if d.get("hub") else None
            hub_line = f"\n\nPart of [[{hub_note.name}]]." if hub_note else ""
            path.write_text(dump_frontmatter({"name": name, "description": str(d.get("description") or it["claim"])[:200],
                                              "type": it["type"], "updated": today()})
                            + f"\n# {title}\n\n{text}{hub_line}\n", encoding="utf-8")
            if hub_note:  # the hub lists its notes, so the new one is reachable from the map
                hub_before = (hub_note.path, hub_note.path.read_text(encoding="utf-8"))
                line = f"- [[{name}]] — {short(str(d.get('description') or title), 100)}"
                _append(hub_note.path, "", line)
                entry.update(hub=hub_note.rel, hub_line=line)
        lib = Library(v.root)
        n = lib.find(path.stem)
        bad = [f for f in lint_note(n, lib) if f.level == "MUST" and f.msg not in must_before] if n else []
        if bad:
            if hub_before:
                hub_before[0].write_text(hub_before[1], encoding="utf-8")
            if before is None:
                path.unlink()
            else:
                path.write_text(before, encoding="utf-8")
            lib = Library(v.root)
            report.append(f"{it['id']}: rolled back ({bad[0].msg}), " + _failed(v, it, f"rolled back: {bad[0].msg}"))
            continue
        rel = path.relative_to(v.root).as_posix()
        inbox.close(v, it["id"], "done", f"{act} {rel}")
        events.log(v, "distill", item=it["id"], action=act, note=path.stem)
        if act == "new":
            entry["after"] = journal.sha(path.read_text(encoding="utf-8"))
        journal.log(v, "fact", note=path.stem, path=rel, **entry)
        if (v.root / ".git").exists() and (v.get("git_owned") or v.get("git_sync")):
            # only the files this item changed: an edit of yours in another note is not part of it
            touched = [rel] + ([entry["hub"]] if entry.get("hub") else [])
            _git(v, "add", "--", *touched)
            _git(v, *nightly.identity(v.root), "commit", "-q", "-m", f"distill: {short(it['claim'], 60)}",
                 "-m", f"Brain-Source: {it['id']}",
                 "--", *touched)
        report.append(f"{it['id']}: {act} → {rel}")
    return report


def _change(v: Vault, item_id: str, fn) -> None:
    items = inbox.items(v, include_done=True)
    for it in items:
        if it["id"] == item_id:
            fn(it)
    from . import ledger
    ledger.save(v, "inbox", items, inbox.RANK)


def _stumbled(v: Vault, item_id: str) -> None:
    """The LLM garbled its answer: not the claim's fault, so it never counts towards dropping it. The item only
    moves behind the others, and a broken CLI can not empty the inbox."""
    _change(v, item_id, lambda it: it.update(stumbles=int(it.get("stumbles") or 0) + 1))


def _failed(v: Vault, it: dict, reason: str) -> str:
    """The answer could not be used for this claim. The next night tries again and is told why; after TRIES_MAX
    nights the claim is dropped and journaled. Returns what happened, for the report."""
    tries = int(it.get("tries") or 0) + 1

    def note(rec: dict) -> None:
        rec.update(tries=tries, hint=short(reason, 200))
        rec.pop("conflict", None)  # until 0.2 such an item waited for you
    _change(v, it["id"], note)
    if tries < TRIES_MAX:
        return f"tried again next night ({tries}/{TRIES_MAX})"
    inbox.close(v, it["id"], "dropped", short(reason, 200))
    journal.log(v, "drop", item=it["id"], type=it.get("type", "fact"), claim=it["claim"], quote=it.get("quote", ""),
                source=it.get("source", ""), why=f"could not be filed in {TRIES_MAX} nights: {short(reason, 160)}")
    return f"dropped after {TRIES_MAX} nights — `brain undo` puts it back"
