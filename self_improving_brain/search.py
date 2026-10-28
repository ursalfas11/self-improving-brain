"""Recall: BM25 over sections + what the brain has learned + the [[link]] graph.

Three signals, all local:
  1. BM25 on note sections (title and description weigh more).
  2. Learned terms: when the agent needed a note that recall did not offer, the words of that
     prompt get attached to the note. Next time the same kind of question finds it.
     Notes that are offered but never opened slowly lose weight (hit rate multiplier).
  3. Graph: the strongest hits pass a share of their score to linked notes, damped by
     1/sqrt(degree*degree) so heavily linked stubs do not pull in everything.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .config import Vault
from .notes import Library, Note, Section
from .util import load_json, terms

K1, B = 1.2, 0.75


@dataclass
class Hit:
    note: Note
    score: float
    section: Section | None
    why: str = ""


class Index:
    def __init__(self, lib: Library, learned: dict | None = None):
        self.lib = lib
        self.learned = learned or {}
        self.docs: list[tuple[str, int, Counter, int]] = []  # (note, section idx, tf, length)
        df: Counter = Counter()
        for name, n in lib.notes.items():
            head = terms(" ".join([n.title, n.description, " ".join(n.tags), name.replace("-", " ")]))
            secs = n.sections or [Section("(intro)", 2, n.body)]
            for i, s in enumerate(secs):
                tf = Counter(terms(s.body))
                for t in terms(s.title):
                    tf[t] += 2
                for t in head:
                    tf[t] += 2
                self.docs.append((name, i, tf, sum(tf.values()) or 1))
                df.update(tf.keys())
        self.N = max(1, len(self.docs))
        self.avg = sum(d[3] for d in self.docs) / self.N
        self.idf = {t: math.log(1 + (self.N - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def _idf(self, t: str) -> float:
        return self.idf.get(t, math.log(1 + self.N))

    def score(self, query: str, *, use_learned: bool = True, graph_beta: float = 0.3,
              seeds: int = 3, include_hubs: bool = True) -> list[Hit]:
        q = list(dict.fromkeys(terms(query)))
        if not q:
            return []
        per_note: dict[str, list[tuple[float, int]]] = {}
        for name, i, tf, ln in self.docs:
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self._idf(t) * f * (K1 + 1) / (f + K1 * (1 - B + B * ln / self.avg))
            if s > 0:
                per_note.setdefault(name, []).append((s, i))

        scores: dict[str, float] = {}
        best_sec: dict[str, int] = {}
        why: dict[str, str] = {}
        for name, lst in per_note.items():
            lst.sort(reverse=True)
            scores[name] = lst[0][0] + (0.2 * lst[1][0] if len(lst) > 1 else 0.0)
            best_sec[name] = lst[0][1]
            why[name] = "text"

        if use_learned and self.learned:
            for name, info in self.learned.items():
                if name not in self.lib.notes:
                    continue
                lt = info.get("terms", {})
                bonus = sum(self._idf(t) * 0.8 * min(lt[t], 3) for t in q if t in lt)
                if bonus:
                    scores[name] = scores.get(name, 0.0) + bonus
                    why[name] = "learned" if why.get(name) != "text" else "text+learned"
                shown, hit = info.get("shown", 0), info.get("hit", 0)
                if name in scores and shown >= 3:
                    scores[name] *= 0.75 + 0.5 * (hit + 1) / (shown + 2)

        if graph_beta > 0 and scores:
            g = self.lib.neighbors()
            top = sorted(scores.items(), key=lambda x: -x[1])[:seeds]
            for seed, s in top:
                for nb in g.get(seed, ()):
                    d = math.sqrt(max(1, len(g.get(seed, ()))) * max(1, len(g.get(nb, ()))))
                    add = graph_beta * s / d
                    if add > 0.05:
                        scores[nb] = scores.get(nb, 0.0) + add
                        why.setdefault(nb, f"linked from {seed}")

        hits = []
        for name, s in sorted(scores.items(), key=lambda x: -x[1]):
            n = self.lib.notes[name]
            if not include_hubs and n.is_hub:
                continue
            sec = None
            if name in best_sec and n.sections:
                sec = n.sections[best_sec[name]]
            hits.append(Hit(n, round(s, 2), sec, why.get(name, "")))
        return hits


def learned_model(v: Vault) -> dict:
    """Cached by `brain nightly`; computed live if no cache exists yet."""
    cached = load_json(v.path("state", "learned.json"), None)
    if cached is not None:
        return cached
    from .metrics import build_learned
    return build_learned(v)


def snippet(section: Section | None, query: str, max_lines: int = 3) -> str:
    if section is None:
        return ""
    q = set(terms(query))
    lines = [l.strip() for l in section.body.splitlines() if l.strip() and not l.startswith("## ")]
    ranked = sorted(lines, key=lambda l: -len(q & set(terms(l))))
    keep = [l for l in ranked[:max_lines] if q & set(terms(l))]
    keep.sort(key=lines.index)
    return "\n".join(keep)
