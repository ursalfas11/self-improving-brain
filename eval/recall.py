#!/usr/bin/env python3
"""How well does recall find the right note? Measured on a gold set: questions with the notes that answer them.

The bundled set is invented: `recall_vault/` holds 40 small notes (English and German) and `recall_gold.jsonl` 125
questions about them, tagged by how the question differs from the note: `exact` (shares the words), `form` (other
word form), `compound`, `typo`, `paraphrase` (shares no word). Measured without learned terms and without the link
graph's help from past reads, so the numbers describe the search itself.

Each question has a `split`: its own field, or a hash of its id. `dev` is what the fusion weights and the threshold
may be tuned on, `test` is never looked at while tuning. Report the test numbers.

  python3 eval/recall.py                          the bundled set, test half, word search against fuzzy
  python3 eval/recall.py --split dev              the tuning half, with every question that was missed
  python3 eval/recall.py --gold my-gold.jsonl --vault ~/brain      your own gold set (see eval/gold_build.py)
  python3 eval/recall.py --thresholds 3,4,5,6,8   what the hook would show at each recall threshold
  python3 eval/recall.py --time                   how long one search takes (median of the questions)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.search import Index  # noqa: E402

HERE = Path(__file__).resolve().parent
GOLD = HERE / "recall_gold.jsonl"
VAULT = HERE / "recall_vault"
GRAPH_BETA, GRAPH_SEEDS = 0.3, 3  # what the hook uses by default


def split_of(row: dict) -> str:
    if row.get("split") in ("dev", "test"):
        return row["split"]
    return "test" if int(hashlib.sha1(str(row["id"]).encode()).hexdigest(), 16) % 2 else "dev"


def load(path: Path = GOLD, split: str = "test") -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("gold") and (split == "all" or split_of(r) == split)]


def rank(idx: Index, question: str, fuzzy: bool) -> list:
    """What the prompt hook ranks: hubs out, the same graph settings."""
    return idx.score(question, graph_beta=GRAPH_BETA, seeds=GRAPH_SEEDS, include_hubs=False, fuzzy=fuzzy)


def measure(idx: Index, rows: list[dict], fuzzy: bool, thresholds: tuple = ()) -> dict:
    """Top-1, top-5 and MRR in percent, by kind, plus the hook's view at each threshold."""
    by: dict = defaultdict(lambda: {"n": 0, "top1": 0, "top5": 0, "mrr": 0.0})
    th = {t: {"right": 0, "wrong": 0, "silent": 0} for t in thresholds}
    missed = []
    for r in rows:
        gold = set(r["gold"])
        hits = rank(idx, r["question"], fuzzy)
        pos = next((i + 1 for i, h in enumerate(hits) if h.note.name in gold), 0)
        for key in ("all", r.get("kind", "?")):
            m = by[key]
            m["n"] += 1
            m["top1"] += pos == 1
            m["top5"] += 0 < pos <= 5
            m["mrr"] += 1.0 / pos if pos else 0.0
        if not pos or pos > 5:
            missed.append((r["question"], r["gold"][0], hits[0].note.name if hits else "-"))
        for t in thresholds:
            if not hits or hits[0].score < t:
                th[t]["silent"] += 1
            elif hits[0].note.name in gold:
                th[t]["right"] += 1
            else:
                th[t]["wrong"] += 1
    out = {k: {"n": m["n"], "top1": 100.0 * m["top1"] / m["n"], "top5": 100.0 * m["top5"] / m["n"],
               "mrr": m["mrr"] / m["n"]} for k, m in by.items()}
    return {"by": out, "threshold": th, "missed": missed}


def timing(idx: Index, rows: list[dict], fuzzy: bool) -> float:
    """Median milliseconds of one search over the questions (index already built)."""
    ts = []
    for r in rows:
        t = time.perf_counter()
        rank(idx, r["question"], fuzzy)
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test", choices=("dev", "test", "all"))
    ap.add_argument("--gold", type=Path, default=GOLD)
    ap.add_argument("--vault", type=Path, default=VAULT, help="folder of notes the gold set refers to")
    ap.add_argument("--thresholds", default="", help="comma separated, e.g. 3,4,5,6,8")
    ap.add_argument("--time", action="store_true")
    a = ap.parse_args()
    rows = load(a.gold, a.split)
    if not rows:
        print("no questions in this split")
        return 1
    lib = Library(a.vault)
    idx = Index(lib, {})
    ths = tuple(float(x) for x in a.thresholds.split(",") if x.strip())
    print(f"{len(rows)} questions ({a.split}), {len(lib.notes)} notes\n")
    res = {name: measure(idx, rows, fz, ths) for name, fz in (("words", False), ("words + fuzzy", True))}
    kinds = ["all"] + sorted({r.get("kind", "?") for r in rows})
    print(f"{'':14}{'n':>4}   " + "   ".join(f"{n:>22}" for n in res))
    print(f"{'':18}   " + "   ".join(f"{'top-1  top-5   MRR':>22}" for _ in res))
    for k in kinds:
        cells = []
        for name in res:
            m = res[name]["by"].get(k)
            cells.append(f"{m['top1']:5.0f}% {m['top5']:5.0f}% {m['mrr']:5.2f}" if m else "")
        n = res["words"]["by"].get(k, {}).get("n", 0)
        print(f"{k:14}{n:>4}   " + "   ".join(f"{c:>22}" for c in cells))
    if ths:
        print("\nHook view at each threshold (top-1 shown / right / wrong):")
        for name in res:
            for t in ths:
                x = res[name]["threshold"][t]
                print(f"  {name:14} >= {t:<5} right {x['right']:3}  wrong {x['wrong']:3}  silent {x['silent']:3}")
    if a.split == "dev":
        print("\nMissed on dev (not in the top 5), with the fuzzy ranking:")
        for q, want, got in res["words + fuzzy"]["missed"]:
            print(f"  {q!r}: wanted {want}, first was {got}")
    if a.time:
        print(f"\nmedian search: words {timing(idx, rows, False):.2f} ms, words + fuzzy {timing(idx, rows, True):.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
