#!/usr/bin/env python3
"""Build a recall gold set from your own sessions, split into a tuning half and a test half.

A gold set is a list of questions with the notes that answer them. Yours comes from what really happened: you
asked your agent something, and the agent then opened notes of your vault. Those are candidates; an LLM judge
reads each question with the full text of the notes and decides which ones were needed. Then `eval/recall.py
--gold <file> --vault <your vault>` measures how well recall finds them.

  python3 eval/gold_build.py candidates --vault ~/brain        sessions -> state/gold-candidates.jsonl (no LLM)
  python3 eval/gold_build.py judge --vault ~/brain             LLM judge -> state/gold.jsonl (asks first; budgeted)
  python3 eval/gold_build.py check state/gold.jsonl            sanity checks of a gold file (no LLM)

Where the files go: `<vault>/.brain/state/`, which the vault's git ignores. **They hold your real questions.**
Never commit or publish them; publish numbers, not the questions.

The split is by session, not by question: one session never lands in both halves, or a tuned weight could learn
a conversation it is later tested on. 50 % dev, 50 % test, a hash of the session id, so it never re-rolls. Tune on
`dev`, report `test`. The judge is a model, not the truth: look at a sample before you trust the numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from self_improving_brain import llm  # noqa: E402
from self_improving_brain.config import open_vault  # noqa: E402
from self_improving_brain.hooks import READ_CMD  # noqa: E402
from self_improving_brain.learn import transcript_files  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.search import Index  # noqa: E402
from self_improving_brain.transcripts import parse  # noqa: E402

MIN_CHARS, MAX_CHARS = 25, 400
NOTE_CAP, POOL = 12000, 3


def split_of_session(session: str) -> str:
    """dev or test, fixed by the session id."""
    return "test" if int(hashlib.sha1(session.encode()).hexdigest(), 16) % 2 else "dev"


def question_id(session: str, question: str) -> str:
    return hashlib.sha1(f"{session}\0{question}".encode()).hexdigest()[:10]


def _read_notes(turn_text: str, tool: str, lib: Library) -> list[str]:
    """Notes a tool call opened: `brain read <note>` in a shell, or a file tool on a note of the vault."""
    try:
        args = json.loads(turn_text)
    except ValueError:
        return []
    if not isinstance(args, dict):
        return []
    refs: list[str] = []
    if tool == "Bash":
        m = READ_CMD.search(str(args.get("command") or ""))
        if m:
            refs.append(m.group(1))
    else:
        path = str(args.get("file_path") or args.get("path") or "")
        if path.endswith(".md"):
            refs.append(path)
    out = []
    for r in refs:
        n = lib.find(Path(r).stem if "/" in r else r)
        if n:
            out.append(n.name)
    return out


def candidates(lib: Library, files: list[Path]) -> list[dict]:
    """One row per question that was followed by at least one note being opened."""
    rows = []
    for f in files:
        tr = parse(f)
        if tr is None or tr.internal:
            continue
        question, read = None, []

        def close():
            if question and read:
                rows.append({"id": question_id(tr.session, question), "session": tr.session[:12], "question": question,
                             "read": list(dict.fromkeys(read)), "split": split_of_session(tr.session)})
        for t in tr.turns:
            if t.role == "user":
                close()
                q = t.text.strip()
                question = q if MIN_CHARS <= len(q) <= MAX_CHARS and not q.startswith("/") else None
                read = []
            elif t.role == "tool_call" and question:
                read += _read_notes(t.text, t.tool, lib)
        close()
    return rows


PROMPT = llm.MARKER + """
You judge the search of a personal knowledge vault. Below: ONE message a user sent to a coding assistant, and the
full text of several notes. Judge by content only.

1. kind of message:
   - knowledge: the message alone names a topic that a note of the vault answers or whose rule it holds.
   - context: only understandable inside the conversation ("yes do that", "and the other one?") or a plain work order.
   - none: small talk, pasted material without a task.
2. per note: central = without this note (or an equal one) the question cannot be answered properly, or its core
   topic is there; partial = side knowledge, a detail or only the frame; no = not needed. Be strict: a note that only
   touches the topic or happens to share words is `no`. Several notes can be central.

Answer with JSON: {"kind": "knowledge|context|none", "notes": [{"name": "...", "verdict": "central|partial|no"}]}
Return every note named below.

MESSAGE: %s

%s"""


def judge_one(v, lib: Library, idx: Index, row: dict) -> dict | None:
    pool = [h.note.name for h in idx.score(row["question"], graph_beta=0, include_hubs=False)
            if h.note.name not in row["read"]][:POOL]  # notes the agent did not open: the judge may reject them too
    parts = []
    for name in row["read"] + pool:
        n = lib.notes.get(name)
        if n is None:
            continue
        text = n.body[:NOTE_CAP] + ("\n[... cut]" if len(n.body) > NOTE_CAP else "")
        parts.append(f"=== NOTE {name} ===\n{n.title}\n{text}")
    if not parts:
        return None
    try:
        with llm.purpose("gold"):
            d = llm.ask_json(v, PROMPT % (row["question"], "\n\n".join(parts)))
    except (llm.NoLLM, ValueError):
        return None
    if not isinstance(d, dict) or d.get("kind") not in ("knowledge", "context", "none"):
        return None
    verdicts = {x.get("name"): x.get("verdict") for x in d.get("notes") or [] if isinstance(x, dict)}
    central = [n for n in row["read"] + pool if verdicts.get(n) == "central"]
    partial = [n for n in row["read"] + pool if verdicts.get(n) == "partial"]
    return {"id": row["id"], "question": row["question"], "gold": central, "also": partial, "kind": d["kind"],
            "split": row["split"], "session": row["session"], "source": "llm-judge"}


def cmd_candidates(a) -> int:
    v = open_vault(a.vault)
    lib = Library(v.root)
    rows = candidates(lib, transcript_files(v))
    out = Path(a.out) if a.out else v.path("state", "gold-candidates.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} questions in {len({r['session'] for r in rows})} sessions -> {out}")
    print("They hold your real questions: keep this file out of git and out of any public repo.")
    return 0


def cmd_judge(a) -> int:
    v = open_vault(a.vault)
    lib = Library(v.root)
    src = Path(a.candidates) if a.candidates else v.path("state", "gold-candidates.jsonl")
    out = Path(a.out) if a.out else v.path("state", "gold.jsonl")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()} if out.exists() else set()
    todo = [r for r in rows if r["id"] not in done][: a.max or None]
    if not llm.available(v):
        print("no llm_cmd configured: `brain config llm_cmd \"<command>\"` (see `brain setup`)")
        return 1
    print(f"judge plan: {len(todo)} questions, one call each of `{str(v.get('llm_cmd')).split()[0]}`; the question and the "
          f"full text of the notes go to that CLI, like the nightly run. Limit: today's budget in `brain` status.")
    if not a.yes and input("run it? [y/N] ").strip().lower() != "y":
        print("not run")
        return 1
    idx = Index(lib, {})  # no learned terms: what the judge sees as "the pool" must not depend on past reads
    n = 0
    with out.open("a", encoding="utf-8") as fh:
        for r in todo:
            if not llm.budget_left(v):
                print("budget used up: run again tomorrow (finished questions are kept)")
                break
            g = judge_one(v, lib, idx, r)
            if g is not None:
                fh.write(json.dumps(g, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
    print(f"{n} judged -> {out}. Next: python3 eval/gold_build.py check {out}")
    return 0


def check(path: Path, lib: Library | None = None) -> list[str]:
    """Problems of a gold file; an empty list is fine."""
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = []
    sessions: dict[str, set] = defaultdict(set)
    for r in rows:
        if r.get("split") not in ("dev", "test"):
            bad.append(f"{r.get('id')}: no split")
        if r.get("session"):
            sessions[r["session"]].add(r.get("split"))
        if lib is not None and r.get("kind") == "knowledge" and not set(r.get("gold") or []) <= set(lib.notes):
            bad.append(f"{r.get('id')}: gold note is not in the vault")
    bad += [f"session {s} is in both halves" for s, halves in sessions.items() if len(halves) > 1]
    usable = [r for r in rows if r.get("gold")]
    for half in ("dev", "test"):
        n = sum(1 for r in usable if r.get("split") == half)
        if n < 50:
            bad.append(f"only {n} usable questions in {half}: numbers will be noisy")
    return bad


def cmd_check(a) -> int:
    lib = Library(open_vault(a.vault).root) if a.vault else None
    bad = check(Path(a.gold), lib)
    print("\n".join(bad) if bad else "gold file ok")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates", parents=[common])
    c.add_argument("--out")
    c.set_defaults(f=cmd_candidates)
    j = sub.add_parser("judge", parents=[common])
    j.add_argument("--candidates")
    j.add_argument("--out")
    j.add_argument("--max", type=int, default=0, help="judge at most this many questions")
    j.add_argument("--yes", action="store_true")
    j.set_defaults(f=cmd_judge)
    k = sub.add_parser("check", parents=[common])
    k.add_argument("gold")
    k.set_defaults(f=cmd_check)
    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
