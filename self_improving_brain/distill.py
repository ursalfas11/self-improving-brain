"""Distill: turn inbox items into wiki edits (Karpathy's LLM-wiki pattern, locally).

Two ways, same rules:
  * `brain distill`          prints a work packet for your agent: each item with the notes it
                             most likely belongs to. The agent edits, then `brain inbox done <id>`.
  * `brain distill --auto`   uses `llm_cmd` to decide APPEND / NEW / SKIP / CONFLICT per item and
                             applies a minimal patch. Every patch is secret-scanned and linted;
                             a failing patch is rolled back. A conflict with existing knowledge is
                             never auto-resolved — it stays in the inbox for you.
"""
from __future__ import annotations

import subprocess

from . import events, inbox, llm
from .config import Vault
from .lint import lint_note
from .notes import Library, atomic_write, dump_frontmatter, parse_frontmatter, set_field
from .search import Index
from .secrets import scan
from .util import short, slugify, today

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
           " `brain inbox done <id>`. Contradiction with existing text? Ask the user first.", ""]
    for it in items:
        out.append(f"[{it['id']}] {it['type']}: {it['claim']}")
        if it.get("quote"):
            out.append(f"    quote: {short(it['quote'], 160)}")
        for h in candidates(lib, it["claim"]):
            out.append(f"    → {h.note.rel}  (score {h.score}{', ' + h.section.title if h.section else ''})")
    return "\n".join(out)


DECIDE = llm.MARKER + """
You maintain a personal Markdown wiki. Decide where a new claim goes.
Rules: one truth per fact (update, never duplicate); keep the existing style; never invent facts.
Return JSON: {"action": "append"|"new"|"skip"|"conflict",
 "note": "<existing note name for append>", "section": "<existing H2 title or empty>",
 "text": "<markdown to add: one bullet or short paragraph>",
 "title": "<for new: note title>", "description": "<for new: one line>",
 "hub": "<for new: best hub note name from the list, or empty>",
 "reason": "<one short sentence>"}
skip = already covered. conflict = contradicts existing text (explain in reason).

Claim (@TYPE@): @CLAIM@
Quote: @QUOTE@

Candidate notes:
@CANDS@

Hubs: @HUBS@
"""


def _append(path, section: str, text: str) -> None:
    raw = set_field(path.read_text(encoding="utf-8"), "updated", today())
    _, body = parse_frontmatter(raw)
    head = raw[: len(raw) - len(body)]
    lines = body.splitlines()
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
    block = text.rstrip().splitlines()
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


def auto(v: Vault, limit: int = 20) -> list[str]:
    if not llm.available(v):
        raise llm.NoLLM("distill --auto needs llm_cmd (see `brain config`)")
    report = []
    lib = Library(v.root)
    hubs = ", ".join(n.name for n in lib.notes.values() if n.is_hub) or "(none)"
    for it in inbox.items(v)[:limit]:
        if it.get("conflict"):
            continue
        cands = candidates(lib, it["claim"])
        ctext = "\n\n".join(f"### {h.note.name} ({h.note.rel})\n{h.note.description}\n"
                            f"{(h.section.body if h.section else '')[:1500]}" for h in cands) or "(none)"
        prompt = (DECIDE.replace("@TYPE@", it["type"]).replace("@CLAIM@", it["claim"])
                  .replace("@QUOTE@", it.get("quote", "")).replace("@HUBS@", hubs).replace("@CANDS@", ctext))
        try:
            d = llm.ask_json(v, prompt)
        except (llm.NoLLM, ValueError) as e:
            report.append(f"{it['id']}: llm error {e}")
            continue
        if not isinstance(d, dict):
            report.append(f"{it['id']}: llm error: expected a JSON object")
            continue
        act = str(d.get("action", "")).lower()
        if act == "skip":
            inbox.close(v, it["id"], "skipped", d.get("reason", ""))
            report.append(f"{it['id']}: skip — {d.get('reason', '')}")
            continue
        if act == "conflict" or act not in ("append", "new"):
            _flag_conflict(v, it["id"], d.get("reason", "unclear"))
            report.append(f"{it['id']}: CONFLICT left for you — {d.get('reason', '')}")
            continue
        text = str(d.get("text", "")).strip()
        if not text or scan(text):
            report.append(f"{it['id']}: refused (empty or secret-like text)")
            continue
        if act == "append":
            target = lib.find(str(d.get("note", "")))
            if target is None:
                report.append(f"{it['id']}: note {d.get('note')!r} not found, left in inbox")
                continue
            must_before = {f.msg for f in lint_note(target, lib) if f.level == "MUST"}
            path, before, hub_before = target.path, target.path.read_text(encoding="utf-8"), None
            _append(path, str(d.get("section", "")), text)
        else:
            title = str(d.get("title") or it["claim"][:60])
            name = slugify(title)
            folder = v.root / FOLDER_FOR.get(it["type"], "20-areas")
            folder.mkdir(parents=True, exist_ok=True)
            path, before, hub_before, must_before = folder / f"{name}.md", None, None, set()
            if path.exists():
                report.append(f"{it['id']}: {path.name} exists, left in inbox")
                continue
            hub_note = lib.find(str(d.get("hub") or "")) if d.get("hub") else None
            hub_line = f"\n\nPart of [[{hub_note.name}]]." if hub_note else ""
            path.write_text(dump_frontmatter({"name": name, "description": str(d.get("description") or it["claim"])[:200],
                                              "type": it["type"], "updated": today()})
                            + f"\n# {title}\n\n{text}{hub_line}\n", encoding="utf-8")
            if hub_note:  # the hub lists its notes, so the new one is reachable from the map
                hub_before = (hub_note.path, hub_note.path.read_text(encoding="utf-8"))
                _append(hub_note.path, "", f"- [[{name}]] — {short(str(d.get('description') or title), 100)}")
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
            report.append(f"{it['id']}: rolled back ({bad[0].msg})")
            continue
        inbox.close(v, it["id"], "done", f"{act} {path.relative_to(v.root)}")
        events.log(v, "distill", item=it["id"], action=act, note=path.stem)
        if (v.root / ".git").exists():
            _git(v, "add", "-A", "--", "*.md")
            _git(v, "commit", "-q", "-m", f"distill: {short(it['claim'], 60)}", "-m", f"Brain-Source: {it['id']}")
        report.append(f"{it['id']}: {act} → {path.relative_to(v.root)}")
    return report


def _flag_conflict(v: Vault, item_id: str, reason: str) -> None:
    from .util import read_jsonl, write_jsonl
    items = read_jsonl(inbox.path(v))
    for it in items:
        if it["id"] == item_id:
            it["conflict"] = reason
    write_jsonl(inbox.path(v), items)
