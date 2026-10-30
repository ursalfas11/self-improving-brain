"""`brain ingest`: sources in, and a brain that notices when a source changes under it.

`brain ingest <file|https-url|folder>` stores each source as a note in `30-resources/sources/`
(plain text, secret-scanned, with where it came from and a digest of what it said) and leaves an
inbox todo to distill it into the notes that own its topics (`brain distill`, or /brain-distill).

Truth maintenance: `brain ingest --refresh` fetches every source again. When one changed, its note
is updated and every note that links to it gets an inbox item "check this still holds", so
knowledge built on an old version of a page does not quietly go stale.
"""
from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import inbox, secrets
from .config import Vault
from .notes import Library, parse_frontmatter, set_field
from .util import digest, short, slugify, today

DEST = "30-resources/sources"
MAX_BYTES = 2_000_000
MAX_CHARS = 80_000
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".org", ".adoc"}


class _Text(HTMLParser):
    """HTML → Markdown-ish text: headings, paragraphs, list items, code; no nav, scripts or styles."""
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "form", "button"}
    BLOCK = {"p", "div", "section", "article", "main", "br", "tr", "table", "blockquote", "pre", "li", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-4]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag == "code":
            self.out.append("`")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "code":
            self.out.append("`")
        elif re.fullmatch(r"h[1-4]", tag) or tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        t = "".join(self.out)
        t = re.sub(r"[ \t]+\n", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def read(src: str) -> tuple[str, str, str]:
    """(title, text, origin) of one file or https URL."""
    if src.startswith("http://"):
        raise ValueError("refusing plain http: use https")
    if src.startswith("https://"):
        req = urllib.request.Request(src, headers={"User-Agent": "self-improving-brain"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(MAX_BYTES + 1)
            ctype = r.headers.get("Content-Type", "")
        if len(raw) > MAX_BYTES:
            raise ValueError("source is larger than 2 MB")
        body = raw.decode("utf-8", "replace")
        origin, is_html = src, "html" in ctype or body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))
    else:
        p = Path(src).expanduser()
        if not p.is_file():
            raise ValueError(f"no such file {src}")
        if p.suffix.lower() not in TEXT_EXT:
            raise ValueError(f"{p.name}: only text sources ({', '.join(sorted(TEXT_EXT))})")
        body, origin, is_html = p.read_text(encoding="utf-8", errors="replace"), str(p.resolve()), p.suffix.lower() in (".html", ".htm")
    if is_html:
        h = _Text()
        h.feed(body)
        title, text = " ".join(h.title.split()), h.text()
    else:
        meta, text = parse_frontmatter(body)
        m = re.search(r"^#\s+(.+)$", text, re.M)
        title = str(meta.get("title") or (m.group(1) if m else Path(origin).stem))
    return title.strip() or Path(origin).stem, text, origin


def _existing(lib: Library) -> dict[str, object]:
    return {str(n.meta.get("source")): n for n in lib.notes.values() if n.type == "source" and n.meta.get("source")}


def add(v: Vault, src: str) -> dict:
    """Store one source; on a known source this is a refresh."""
    title, text, origin = read(src)
    found = secrets.scan(text)
    if found:
        raise ValueError(f"{origin}: looks like it contains a secret ({', '.join(k for k, _ in found[:3])}) — not stored")
    h = digest(text)[:16]
    lib = Library(v.root)
    old = _existing(lib).get(origin)
    cut = text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n\n…(cut: the source is longer)"
    if old is not None:
        if str(old.meta.get("digest")) == h:
            return {"status": "unchanged", "note": old.name, "path": old.path}
        t = old.path.read_text(encoding="utf-8")
        head = t[: t.index("\n<!-- brain:source -->")] if "\n<!-- brain:source -->" in t else t
        t = set_field(set_field(set_field(head, "digest", h), "changed", today()), "updated", today())
        old.path.write_text(t.rstrip("\n") + "\n<!-- brain:source -->\n" + cut + "\n", encoding="utf-8")
        flagged = []
        for n in lib.notes.values():
            if old.name in n.links and n.name != old.name:
                try:
                    inbox.add(v, "todo", f"[[{n.name}]] relies on [[{old.name}]], which changed on {today()} — check that it still holds",
                              note=n.name, source="ingest")
                    flagged.append(n.name)
                except inbox.Rejected:
                    pass
        return {"status": "changed", "note": old.name, "path": old.path, "flagged": flagged}
    name = "src-" + slugify(title)[:60].strip("-")
    while lib.find(name):
        name += "-2"
    first = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), "")
    desc = short(f"Source: {title}" + (f" - {first}" if first else ""), 200).replace("\n", " ")
    dest = v.root / DEST / f"{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = (f"---\nname: {name}\ndescription: {desc}\ntype: source\nsource: {origin}\nfetched: {today()}\n"
            f"digest: {h}\nupdated: {today()}\n---\n")
    dest.write_text(meta + f"# {title}\n\nIngested from {origin} on {today()}. Part of [[hub-brain]]; the notes built on it "
                    f"link here, so a change in the source flags them.\n\n<!-- brain:source -->\n{cut}\n", encoding="utf-8")
    try:
        inbox.add(v, "todo", f"Distill [[{name}]] ({short(title, 80)}) into the notes that own its topics, and link them to it",
                  note=name, source="ingest")
    except inbox.Rejected:
        pass
    return {"status": "new", "note": name, "path": dest}


def sources(v: Vault, folder: str) -> list[str]:
    root = Path(folder).expanduser()
    return [str(p) for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in TEXT_EXT
            and not any(x.startswith(".") for x in p.relative_to(root).parts)][:200]


def refresh(v: Vault) -> list[dict]:
    out = []
    for origin in sorted(_existing(Library(v.root))):
        try:
            out.append({"source": origin, **add(v, origin)})
        except (ValueError, OSError) as e:
            out.append({"source": origin, "status": f"error: {e}"})
    return out
