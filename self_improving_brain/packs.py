"""Lesson packs: rules other people already paid for, one command away.

A pack is one Markdown note full of lesson callouts (the bundled ones live in `packs/`).
`brain pack add <name|file|https-url>` copies it into the vault as `30-resources/packs/pack-<name>.md`.
Its lessons are trusted because you added them on purpose, and from then on they are yours: edit,
sharpen, promote or delete them like any other lesson. `brain pack export` turns lessons from your
vault into a pack you can share: dates and evidence (your own words) stripped, secret-scanned first.
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from . import secrets, trust
from .config import Vault
from .notes import MECHANISMS, Lesson, Library, parse_frontmatter, parse_lessons
from .util import digest, load_json, now_iso, save_json, slugify, today

BUILTIN = Path(__file__).parent / "packs"
DEST = "30-resources/packs"
MAX_BYTES = 200_000
_HEAD = re.compile(r"^> \[!lesson\][+-]?\s*(\S+)")


def builtin() -> dict[str, Path]:
    return {p.stem: p for p in sorted(BUILTIN.glob("*.md"))}


def _ledger(v: Vault) -> dict:
    d = load_json(v.path("packs.json"), {})
    return d if isinstance(d, dict) else {}


def parse(text: str) -> tuple[dict, list[Lesson]]:
    meta, body = parse_frontmatter(text)
    return meta, parse_lessons(body, str(meta.get("name") or "pack"))


def problems(lessons: list[Lesson]) -> list[str]:
    """Reasons a lesson cannot be installed as written."""
    out = []
    for l in lessons:
        if not l.id.startswith("L-"):
            out.append(f"{l.id}: lesson ids start with L-")
        if l.mechanism not in MECHANISMS:
            out.append(f"{l.id}: unknown mechanism {l.mechanism}")
        if len(l.rule) > 240:
            out.append(f"{l.id}: rule longer than 240 chars")
        for k in ("commands", "paths", "check"):
            try:
                re.compile(getattr(l, k) or "")
            except re.error as e:
                out.append(f"{l.id}: {k} is not a valid regex ({e})")
    return out


def warnings(lessons: list[Lesson]) -> list[str]:
    """What to look at before trusting a pack from someone else."""
    out = []
    for l in lessons:
        if l.mechanism == "always":
            out.append(f"{l.id} is an always-lesson: it goes into every session")
        if re.search(r"https?://|curl|wget|\bssh\b|upload|send|post to", l.rule, re.I):
            out.append(f"{l.id} mentions a URL or sending data: {l.rule}")
    return out


def fetch(src: str) -> tuple[str, str, str]:
    """(name, text, origin) for a bundled pack name, a local file or an https URL."""
    packs = builtin()
    if src in packs:
        return src, packs[src].read_text(encoding="utf-8"), "builtin"
    if src.startswith("http://"):
        raise ValueError("refusing a plain-http pack: use https")
    if src.startswith("https://"):
        with urllib.request.urlopen(src, timeout=15) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("pack is larger than 200 KB")
        text, origin = raw.decode("utf-8"), src
    else:
        p = Path(src).expanduser()
        if not p.is_file():
            raise ValueError(f"no pack {src!r} — bundled packs: {', '.join(packs)}")
        text, origin = p.read_text(encoding="utf-8"), str(p.resolve())
    meta, _ = parse(text)
    name = slugify(str(meta.get("pack") or str(meta.get("name") or "").removeprefix("pack-") or Path(src).stem))
    return name, text, origin


def _drop(text: str, ids: set[str]) -> str:
    """The pack text without the callouts of these lesson ids."""
    out, skip = [], False
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            skip = m.group(1) in ids
        elif skip and not line.startswith(">"):
            skip = False
        if not skip:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip("\n") + "\n"


def target(v: Vault, name: str) -> Path:
    return v.root / DEST / f"pack-{name}.md"


def plan(v: Vault, src: str) -> dict:
    """What `add` would do, without writing anything."""
    name, text, origin = fetch(src)
    meta, lessons = parse(text)
    lib = Library(v.root)
    mine = {l.id: l.note for l in lib.lessons() if l.note != f"pack-{name}"}
    clash = {l.id for l in lessons if l.id in mine}
    return {"name": name, "origin": origin, "version": str(meta.get("pack_version") or "1"),
            "description": str(meta.get("description") or ""), "text": text,
            "lessons": [l for l in lessons if l.id not in clash], "skipped": sorted(clash),
            "problems": problems(lessons), "warnings": warnings(lessons) if origin != "builtin" else [],
            "installed": _ledger(v).get(name)}


def add(v: Vault, src: str, force: bool = False) -> dict:
    p = plan(v, src)
    if p["problems"]:
        raise ValueError("pack has invalid lessons:\n  " + "\n  ".join(p["problems"]))
    if not p["lessons"]:
        raise ValueError("nothing to add: every lesson in this pack is already in your vault")
    dest = target(v, p["name"])
    if dest.exists() and not force:
        rec = p["installed"] or {}
        if digest(dest.read_text(encoding="utf-8")) != rec.get("h"):
            raise ValueError(f"you edited {dest.relative_to(v.root)} — `--force` replaces your edits with this version")
    text = _drop(p["text"], set(p["skipped"])).replace("{{TODAY}}", today())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    _, lessons = parse(text)
    trust.record(v, lessons, "pack:" + p["name"])
    led = _ledger(v)
    led[p["name"]] = {"version": p["version"], "origin": p["origin"], "h": digest(text), "t": now_iso(),
                      "lessons": [l.id for l in lessons]}
    save_json(v.path("packs.json"), led)
    p["path"] = dest
    return p


def remove(v: Vault, name: str, force: bool = False) -> Path:
    dest = target(v, name)
    led = _ledger(v)
    if not dest.exists():
        raise ValueError(f"pack {name!r} is not installed")
    if not force and digest(dest.read_text(encoding="utf-8")) != (led.get(name) or {}).get("h"):
        raise ValueError(f"you edited {dest.relative_to(v.root)} — `--force` removes it anyway")
    dest.unlink()
    led.pop(name, None)
    save_json(v.path("packs.json"), led)
    return dest


def listing(v: Vault) -> list[dict]:
    led = _ledger(v)
    out = []
    for name, path in builtin().items():
        meta, lessons = parse(path.read_text(encoding="utf-8"))
        rec = led.get(name)
        out.append({"name": name, "description": str(meta.get("description") or ""), "lessons": len(lessons),
                    "version": str(meta.get("pack_version") or "1"), "installed": bool(rec and target(v, name).exists()),
                    "outdated": bool(rec and str(rec.get("version")) != str(meta.get("pack_version") or "1"))})
    for name, rec in led.items():
        if name not in builtin() and target(v, name).exists():
            out.append({"name": name, "description": rec.get("origin", ""), "lessons": len(rec.get("lessons", [])),
                        "version": rec.get("version", "1"), "installed": True, "outdated": False})
    return out


def export(v: Vault, name: str, ids: list[str], note: str = "", description: str = "") -> str:
    """A shareable pack from your lessons. Evidence and dates are dropped; secrets block the export."""
    lib = Library(v.root)
    picked = [l for l in lib.lessons() if l.id in ids or (note and l.note == note)]
    missing = [i for i in ids if i not in {l.id for l in picked}]
    if missing:
        raise ValueError(f"no lesson {', '.join(missing)}")
    if not picked:
        raise ValueError("pick lessons with --lesson <id> or --note <note>")
    name = slugify(name)
    callouts = []
    for l in picked:
        c = Lesson(id=l.id, note=l.note, rule=l.rule, mechanism=l.mechanism, tools=l.tools, commands=l.commands,
                   paths=l.paths, words=l.words, check=l.check, since="{{TODAY}}")
        callouts.append(c.as_callout())
    desc = description or f"{len(picked)} lessons exported from a self-improving-brain vault"
    text = (f"---\nname: pack-{name}\ndescription: {desc}\ntype: resource\npack: {name}\npack_version: 1\n"
            f"updated: {{{{TODAY}}}}\n---\n# Pack: {name}\n\n{desc}\nPart of [[hub-brain]]. "
            f"Install with `brain pack add <this file or its https URL>`.\n\n" + "\n\n".join(callouts) + "\n")
    found = secrets.scan(text)
    if found:
        raise ValueError("export blocked — possible secret: " + ", ".join(k for k, _ in found[:3]))
    return text
