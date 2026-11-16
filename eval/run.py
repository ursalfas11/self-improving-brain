#!/usr/bin/env python3
"""How well does the brain notice that you corrected your agent? Measured, per language.

eval/corrections.jsonl holds hand-labelled replies a user gave to a coding agent, in six languages:
`correction` (the agent got something wrong; `durable` when it should become a lasting rule),
`preference` (how things should be done from now on) and `none` (everything else, including replies that
only look like a correction: "no, that's fine", "stop, I'm in the wrong folder", "why a set? just curious").

The detector (`learn.classify`, no LLM) flags a reply when its score reaches the draft threshold. A flag is
right when the label is `correction` or `preference`.

Messages are split by a hash of their id: `dev` is what the patterns were tuned on, `test` was never looked
at while tuning. Report the test numbers.

  python3 eval/run.py                 the test split (what we publish)
  python3 eval/run.py --split dev     the split to tune on, with every miss and false alarm listed
  python3 eval/run.py --judge         also run the nightly LLM judge on the flagged test messages
                                      (spends LLM budget: prints the plan and asks first; --yes skips that)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from self_improving_brain import learn  # noqa: E402
from self_improving_brain.transcripts import Turn  # noqa: E402

DATA = Path(__file__).resolve().parent / "corrections.jsonl"
LANGS = ("en", "de", "es", "fr", "pt", "it")


def split_of(item_id: str) -> str:
    return "test" if int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 2 else "dev"


def load(split: str = "test") -> list[dict]:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if split == "all" or split_of(r["id"]) == split]


def flag(item: dict) -> tuple[bool, float, str]:
    """(flagged, score, kind) for one reply, exactly as `brain` scans a transcript."""
    turns = [Turn("assistant", item["agent"]), Turn("user", item["user"])]
    got = learn.classify(turns, 1)
    if not got:
        return False, 0.0, ""
    kind, score, _ = got
    return score >= learn.DRAFT_MIN, score, kind


def measure(rows: list[dict]) -> dict:
    """Precision and recall per language and overall."""
    out = {}
    for lang in (*LANGS, "all"):
        rs = [r for r in rows if lang == "all" or r["lang"] == lang]
        if not rs:
            continue
        tp = fp = fn = tn = durable = durable_hit = 0
        for r in rs:
            hit = flag(r)[0]
            real = r["label"] != "none"
            tp += hit and real
            fp += hit and not real
            fn += real and not hit
            tn += not hit and not real
            if r.get("durable"):
                durable += 1
                durable_hit += hit
        out[lang] = {"n": len(rs), "positives": tp + fn, "flagged": tp + fp, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                     "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                     "durable_recall": durable_hit / durable if durable else None}
    return out


def pct(x) -> str:
    return "  –" if x is None else f"{100 * x:3.0f} %"


def table(res: dict, title: str) -> str:
    lines = [title, f"{'lang':<5}{'msgs':>6}{'real':>6}{'flagged':>9}{'precision':>11}{'recall':>8}{'lasting':>9}"]
    for lang, m in res.items():
        lines.append(f"{lang:<5}{m['n']:>6}{m['positives']:>6}{m['flagged']:>9}{pct(m['precision']):>11}"
                     f"{pct(m['recall']):>8}{pct(m['durable_recall']):>9}")
    lines.append("real = correction or preference · lasting = recall on the ones that should become a rule")
    return "\n".join(lines)


def mistakes(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        hit, score, kind = flag(r)
        real = r["label"] != "none"
        if hit != real:
            what = "MISS " if real else "FALSE"
            lines.append(f"{what} {r['id']:<7} {r['label']:<10} {score:.2f} {kind:<10} {r['user'][:110]!r}")
    return "\n".join(lines) or "no mistakes"


# --- the LLM judge on top --------------------------------------------------------------------

def judge(rows: list[dict], assume_yes: bool) -> dict:
    """Run the real nightly judge on the flagged messages, in a throwaway vault. Returns verdict counts."""
    from self_improving_brain import llm
    from self_improving_brain.config import Vault
    from self_improving_brain.util import append_jsonl, read_jsonl
    flagged = [r for r in rows if flag(r)[0]]
    cmd = llm.detect()
    if not cmd:
        sys.exit("no LLM CLI found (claude, codex or ollama)")
    calls = -(-len(flagged) // 20)
    print(f"judge plan: {len(flagged)} flagged messages in {calls} call(s) of `{cmd.split()[0]}`; "
          f"with Haiku that is a few cents. A throwaway vault, nothing else is touched.")
    if not assume_yes and input("run it? [y/N] ").strip().lower() != "y":
        sys.exit("not run")
    with tempfile.TemporaryDirectory() as tmp:
        # a throwaway vault and machine config: the CLI login of `claude` stays, nothing of yours is written
        os.environ.update(BRAIN_VAULT=f"{tmp}/vault", XDG_CONFIG_HOME=f"{tmp}/config", XDG_CACHE_HOME=f"{tmp}/cache")
        from self_improving_brain.cli import main as cli
        cli(["init", f"{tmp}/vault"])
        v = Vault(Path(tmp) / "vault")
        v.set("llm_cmd", cmd)
        v.set("llm_budget_usd", 0.5)
        for r in flagged:  # one draft per message, as `brain` would write it; one session each, so no grouping by session
            _, score, kind = flag(r)
            append_jsonl(learn.drafts_path(v), {"id": "d-" + r["id"], "t": "2026-10-01T10:00:00", "status": "open",
                                                "score": score, "kind": "preference" if kind == "preference" else "lesson",
                                                "quote": r["user"], "context": "agent: " + r["agent"][:200],
                                                "session": r["id"], "source": "claude"})
        learn.judge(v, batch=20, batches=calls + 1)
        verdicts = {d["id"][2:]: d for d in read_jsonl(learn.drafts_path(v))}
        spent = llm.spent(v)
    by = {r["id"]: r for r in flagged}
    table_ = Counter()
    for rid, d in verdicts.items():
        r = by[rid]
        truth = "lasting" if r.get("durable") else ("one-off" if r["label"] == "correction" else "none")
        verdict = {"held": d.get("verdict", "rule"), "once": "once", "noise": "noise"}.get(d.get("status"), d.get("status"))
        table_[(truth, verdict)] += 1
    learned = sum(n for (t, v_), n in table_.items() if v_ in ("rule", "fact"))
    good = sum(n for (t, v_), n in table_.items() if t == "lasting" and v_ in ("rule", "fact"))
    return {"messages": len(flagged), "matrix": {f"{t} → {v_}": n for (t, v_), n in sorted(table_.items())},
            "learned_precision": good / learned if learned else None,
            "lasting_kept": good / max(1, sum(n for (t, _), n in table_.items() if t == "lasting")),
            "cost": spent}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=("test", "dev", "all"), default="test")
    ap.add_argument("--judge", action="store_true", help="also run the LLM judge on the flagged messages (costs a few cents)")
    ap.add_argument("--yes", action="store_true", help="do not ask before spending LLM budget")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = load(a.split)
    res = measure(rows)
    out = {"split": a.split, "messages": len(rows), "detector": res}
    if a.judge:
        out["judge"] = judge(rows, a.yes)
    if a.json:
        print(json.dumps(out, indent=1))
        return 0
    print(table(res, f"CORRECTION DETECTOR · no LLM · {a.split} split ({len(rows)} of {len(load('all'))} messages)"))
    if a.split == "dev":
        print("\n" + mistakes(rows))
    if a.judge:
        j = out["judge"]
        print(f"\nJUDGE on the {j['messages']} flagged messages (truth → verdict)")
        for k, n in j["matrix"].items():
            print(f"  {k:<22}{n:>4}")
        print(f"  kept as a rule or fact, and it was a lasting one: {pct(j['learned_precision'])}")
        print(f"  lasting ones the judge kept: {pct(j['lasting_kept'])}")
        print(f"  cost: ${j['cost']['usd']:.4f} · {j['cost']['calls']} calls · {j['cost']['tokens']:,} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
