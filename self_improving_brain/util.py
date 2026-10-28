"""Small shared helpers. Standard library only."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import socket
import unicodedata
from pathlib import Path

WORD = re.compile(r"[\w][\w\-\.]*[\w]|[\w]", re.UNICODE)

STOPWORDS = set("""
a an and are as at be but by can do does for from has have how i if in into is it its me my
not of on or our so than that the their them then there these they this to us was we were what
when where which who why will with you your yes no ok okay please just also again now still
der die das den dem des ein eine einen einem einer und oder aber ist sind war wie was wer wo
ich du er sie es wir ihr mit von zu im in am an auf aus bei für nicht noch nur auch dann doch
mal schon hier da so ja nein bitte mir mich dir dich uns kann soll muss hat haben wird
""".split())


def today() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def iso_week(date_str: str) -> str:
    try:
        d = _dt.date.fromisoformat(date_str[:10])
    except ValueError:
        return "?"
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def days_since(date_str: str) -> int | None:
    try:
        d = _dt.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return None
    return (_dt.date.today() - d).days


def host() -> str:
    """Per-machine id. Each machine writes its own event file -> no merge conflicts."""
    name = os.environ.get("BRAIN_HOST") or socket.gethostname().split(".")[0] or "host"
    return re.sub(r"[^A-Za-z0-9_-]", "-", name).lower()


def fold(s: str) -> str:
    """Lowercase and strip accents so 'Prüfung' matches 'prufung' and 'pruefung' roughly."""
    s = unicodedata.normalize("NFC", s).lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def terms(text: str) -> list[str]:
    out = []
    for w in WORD.findall(fold(text)):
        w = w.strip(".-")
        if len(w) < 2 or w in STOPWORDS or w.isdigit():
            continue
        out.append(w)
    return out


def tokens_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def short(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def slugify(text: str) -> str:
    s = fold(text)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "note"


def digest(*parts: str) -> str:
    h = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return h[:10]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
