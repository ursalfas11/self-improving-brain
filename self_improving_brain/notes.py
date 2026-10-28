"""Notes: Markdown files with a small frontmatter, H2 sections, [[wikilinks]] and lesson callouts.

The format is plain Obsidian-flavoured Markdown, so the vault opens in Obsidian, VS Code or
any editor. Nothing here needs a database.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .util import tokens_estimate

SKIP_DIRS = {".brain", ".git", ".obsidian", ".trash", "node_modules", ".github"}
SKIP_FILES = {"INDEX.md", "README.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md"}

FENCE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
LESSON_HEAD = re.compile(r"^>\s*\[!lesson\][+-]?\s*(\S+)?\s*(.*)$", re.I)
LESSON_FIELD = re.compile(r"^>\s*([a-z_]+)\s*:\s*(.*)$", re.I)

LESSON_KEYS = ("rule", "mechanism", "tools", "commands", "paths", "words", "since", "evidence", "class")
MECHANISMS = ("always", "reflex", "guard", "skill")


@dataclass
class Section:
    title: str
    level: int
    body: str

    @property
    def tokens(self) -> int:
        return tokens_estimate(self.body)


@dataclass
class Lesson:
    id: str
    note: str
    rule: str = ""
    mechanism: str = "reflex"
    tools: list[str] = field(default_factory=list)
    commands: str = ""
    paths: str = ""
    words: list[str] = field(default_factory=list)
    since: str = ""
    evidence: str = ""
    klass: str = ""

    def as_callout(self) -> str:
        lines = [f"> [!lesson]- {self.id}", f"> rule: {self.rule}", f"> mechanism: {self.mechanism}"]
        if self.tools:
            lines.append("> tools: " + ", ".join(self.tools))
        if self.commands:
            lines.append(f"> commands: {self.commands}")
        if self.paths:
            lines.append(f"> paths: {self.paths}")
        if self.words:
            lines.append("> words: " + ", ".join(self.words))
        if self.klass:
            lines.append(f"> class: {self.klass}")
        if self.since:
            lines.append(f"> since: {self.since}")
        if self.evidence:
            lines.append(f"> evidence: {self.evidence}")
        return "\n".join(lines)


@dataclass
class Note:
    path: Path
    rel: str
    name: str
    meta: dict
    title: str
    body: str
    sections: list[Section]
    links: list[str]
    lessons: list[Lesson]

    @property
    def description(self) -> str:
        return str(self.meta.get("description", "")).strip()

    @property
    def type(self) -> str:
        return str(self.meta.get("type", "")).strip()

    @property
    def is_hub(self) -> bool:
        return self.type == "hub" or self.rel.lower().startswith("00-hubs/")

    @property
    def updated(self) -> str:
        return str(self.meta.get("updated", "")).strip()

    @property
    def tags(self) -> list[str]:
        t = self.meta.get("tags", [])
        return t if isinstance(t, list) else [x.strip() for x in str(t).split(",") if x.strip()]

    @property
    def tokens(self) -> int:
        return tokens_estimate(self.body)


# --- frontmatter: a deliberately small YAML subset ------------------------------------------
# Reading understands: key: value, quoted values, [a, "b, c"], - item lists, > and | block scalars,
# [[wikilinks]] as plain strings. Writing never re-serialises an existing frontmatter: only the
# `updated:` line is replaced or inserted (set_field), everything else stays byte-for-byte.

FM_CLOSE = re.compile(r"^---\s*$", re.M)


def _split_list(inner: str) -> list[str]:
    items, buf, quote = [], "", ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    return [x for x in items if x.strip()]


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return inner.replace('\\"', '"') if v[0] == '"' else inner.replace("''", "'")
    if v.startswith("[[") and v.endswith("]]"):
        return v
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in _split_list(v[1:-1])]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def _fm_bounds(text: str) -> tuple[int, int] | None:
    """(start of raw, end of raw) or None. The closing fence must be a line of its own."""
    if not text.startswith("---"):
        return None
    first_nl = text.find("\n")
    if first_nl < 0 or text[:first_nl].strip() != "---":
        return None
    m = FM_CLOSE.search(text, first_nl + 1)
    if not m:
        return None
    return first_nl + 1, m.start()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    b = _fm_bounds(text)
    if not b:
        return {}, text
    raw = text[b[0]:b[1]]
    close_end = text.find("\n", b[1])
    body = text[close_end + 1:] if close_end >= 0 else ""
    meta: dict = {}
    key, block, block_lines = None, "", []

    def flush():
        nonlocal block, block_lines
        if key and block:
            meta[key] = (" " if block.startswith(">") else "\n").join(x.strip() for x in block_lines).strip()
        block, block_lines = "", []

    for line in raw.splitlines():
        if block and (line.startswith((" ", "\t")) or not line.strip()):
            block_lines.append(line)
            continue
        flush()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- ") and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_scalar(stripped[2:]))
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val[:1] in (">", "|") and val.rstrip("+-") in (">", "|"):
                block = val
                meta[key] = ""
            else:
                meta[key] = _scalar(val) if val else []
    flush()
    return meta, body


def _fmt(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    s = str(v)
    if s and (s[0] in "[{'\"&*!|>%@`" or ": " in s or " #" in s or s.endswith(":")) and not s.startswith("[["):
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def dump_frontmatter(meta: dict) -> str:
    """Only for NEW notes. Existing notes are edited with set_field."""
    return "---\n" + "".join(f"{k}: {_fmt(v)}\n" for k, v in meta.items()) + "---\n"


def set_field(text: str, key: str, value: str) -> str:
    """Replace or insert one top-level `key:` line, leaving the rest of the frontmatter untouched."""
    b = _fm_bounds(text)
    if not b:
        return text
    raw = text[b[0]:b[1]]
    line = f"{key}: {_fmt(value)}"
    rx = re.compile(rf"^{re.escape(key)}:.*$", re.M)
    raw = rx.sub(lambda _: line, raw, count=1) if rx.search(raw) else (raw + ("" if raw.endswith("\n") or not raw else "\n") + line + "\n")
    return text[:b[0]] + raw + text[b[1]:]


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- body structure -----------------------------------------------------------------------

def split_sections(body: str) -> tuple[str, list[Section]]:
    title = ""
    sections: list[Section] = []
    cur_title, cur_level, buf = "(intro)", 2, []
    in_code = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("# ") and not title:
            title = line[2:].strip()
            continue
        if not in_code and line.startswith("## "):
            if buf and "".join(buf).strip():
                sections.append(Section(cur_title, cur_level, "\n".join(buf).strip()))
            cur_title, cur_level, buf = line[3:].strip(), 2, [line]
            continue
        buf.append(line)
    if buf and "".join(buf).strip():
        sections.append(Section(cur_title, cur_level, "\n".join(buf).strip()))
    return title, sections


def parse_lessons(body: str, note_name: str) -> list[Lesson]:
    lessons: list[Lesson] = []
    cur: Lesson | None = None
    for line in body.splitlines():
        m = LESSON_HEAD.match(line)
        if m:
            cur = Lesson(id=(m.group(1) or f"L-{note_name}-{len(lessons)+1}"), note=note_name)
            lessons.append(cur)
            continue
        if cur is None:
            continue
        if not line.startswith(">"):
            cur = None
            continue
        f = LESSON_FIELD.match(line)
        if not f:
            continue
        k, v = f.group(1).lower(), f.group(2).strip()
        if k == "rule":
            cur.rule = v
        elif k == "mechanism":
            cur.mechanism = v.lower() if v.lower() in MECHANISMS else "reflex"
        elif k == "tools":
            cur.tools = [x.strip() for x in v.split(",") if x.strip()]
        elif k == "commands":
            cur.commands = v
        elif k == "paths":
            cur.paths = v
        elif k == "words":
            cur.words = [x.strip() for x in v.split(",") if x.strip()]
        elif k == "since":
            cur.since = v
        elif k == "evidence":
            cur.evidence = v
        elif k == "class":
            cur.klass = v
    return [l for l in lessons if l.rule]


def strip_code(body: str) -> str:
    """Links and lessons inside code are examples, not real ones."""
    return INLINE_CODE.sub("", FENCE.sub("", body))


def load_note(path: Path, root: Path) -> Note | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta, body = parse_frontmatter(text)
    rel = path.relative_to(root).as_posix()
    name = str(meta.get("name") or path.stem)
    title, sections = split_sections(body)
    prose = strip_code(body)
    links = []
    for m in LINK.finditer(prose):
        target = m.group(1).strip()
        if target and target not in links and target != name:
            links.append(target)
    return Note(path, rel, name, meta, title or path.stem, body, sections, links, parse_lessons(FENCE.sub("", body), name))


def iter_note_paths(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        top = Path(dirpath) == root
        for f in sorted(filenames):
            if f.endswith(".md") and not (top and f in SKIP_FILES):
                yield Path(dirpath) / f


class Library:
    """All notes of a vault, loaded once per process."""

    def __init__(self, root: Path):
        self.root = root
        self.notes: dict[str, Note] = {}
        self.by_stem: dict[str, str] = {}
        for p in iter_note_paths(root):
            n = load_note(p, root)
            if n is None:
                continue
            self.notes[n.name] = n
            self.by_stem[p.stem.lower()] = n.name

    def find(self, ref: str) -> Note | None:
        ref = ref.strip().removesuffix(".md")
        if ref in self.notes:
            return self.notes[ref]
        key = Path(ref).stem.lower()
        if key in self.by_stem:
            return self.notes[self.by_stem[key]]
        low = {k.lower(): k for k in self.notes}
        if ref.lower() in low:
            return self.notes[low[ref.lower()]]
        hits = [k for k in self.notes if ref.lower() in k.lower()]
        return self.notes[hits[0]] if len(hits) == 1 else None

    def resolve(self, link: str) -> Note | None:
        return self.notes.get(link) or self.notes.get(self.by_stem.get(link.lower(), ""), None)

    def lessons(self) -> list[Lesson]:
        out = []
        for n in self.notes.values():
            out.extend(n.lessons)
        return out

    def neighbors(self) -> dict[str, set[str]]:
        """Undirected link graph without hubs (hubs would connect everything to everything)."""
        g: dict[str, set[str]] = {k: set() for k, n in self.notes.items() if not n.is_hub}
        for k, n in self.notes.items():
            if n.is_hub:
                continue
            for link in n.links:
                t = self.resolve(link)
                if t and not t.is_hub and t.name != k:
                    g[k].add(t.name)
                    g.setdefault(t.name, set()).add(k)
        return g
