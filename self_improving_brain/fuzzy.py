"""Fuzzy recall: character 4-grams, fused into the word search.

BM25 finds only what the question says word for word. Someone types "api key" and the note says "API keys";
"cartonbild" for "cartonbilder"; "deploymnt" for "deployment". Character 4-grams (with word boundaries) catch
word forms, compounds and typos. Two signals are fused onto the BM25 scale, so `recall_threshold` stays a score:

  1. 4-gram cosine (standard library, always on): one weighted vector per note (name x4, description x5,
     headings x2, body x1, tf-idf). It is built with the BM25 index and cached with it, and a note that changed
     is the only one that gets counted again.
  2. dense vectors (optional, see dense.py): only when a local embedding model answers.

Fusion: every signal adds `weight * best_word_score * (cosine / best_cosine) ** POWER` to a note. Whoever leads
a signal gets a share of the best word score, whoever trails gets almost nothing. A question the words miss
entirely is still ordered, but with small scores that never reach the threshold on their own.
"""
from __future__ import annotations

import array
import math
import re
from collections import Counter

from .util import STOPWORDS, digest, fold

N = 4
FIELD_WEIGHTS = (4, 5, 2, 1)  # name, description, headings, body
WEIGHT_FUZZY, WEIGHT_FUZZY_WITH_DENSE, WEIGHT_DENSE, POWER = 0.8, 0.4, 0.8, 4
SCALE_MIN = 4.0  # best word score is at least this when fusing: no word hit still lets the signals order
CANDIDATES = 12  # how many top notes per signal get the costly part of the dense signal

# words that make a question a question, not a topic
CHATTER = set("""
hey hi hello thanks thank cool nice need want let lets know tell show give make fix look check
could would should might may about something anything thing stuff really maybe actually
hallo danke mach machen zeig zeige schau guck gib sag brauche brauchen koennte wuerde
""".split())

_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)


def grams(text: str, question: bool = False) -> list[str]:
    """Character 4-grams per word, with `_` marking the word boundary. A question drops filler words."""
    out: list[str] = []
    for w in _WORD.findall(fold(text or "")):
        if w.isdigit() or w in STOPWORDS or (question and w in CHATTER):
            continue
        w = f"_{w}_"
        out += [w] if len(w) <= N else [w[i:i + N] for i in range(len(w) - N + 1)]
    return out


def note_sig(note) -> str:
    """Changes whenever what the signals read changes."""
    return digest(note.name, note.title, note.description, note.body)


def _note_counts(note) -> Counter:
    wn, wd, wh, wb = FIELD_WEIGHTS
    heads = " ".join(s.title for s in note.sections if s.title != "(intro)")
    c = Counter(grams(note.body))  # the bulk of the text: counted in one pass, weight wb
    if wb != 1:
        for g in c:
            c[g] *= wb
    for text, w in ((note.name.replace("-", " ") + " " + note.title, wn), (note.description, wd), (heads, wh)):
        for g in grams(text):
            c[g] += w
    return c


class FuzzyIndex:
    """4-gram tf-idf vectors of all non-hub notes as an inverted index. It pickles with the BM25 index and is read
    on every prompt, so it is packed: one `bytes` per gram (note numbers, then weights) instead of a list of tuples,
    and the idf of a gram follows from how many notes carry it. The counts are dropped after the build."""

    def __init__(self, lib):
        counts = {name: _note_counts(n) for name, n in lib.notes.items() if not n.is_hub}
        self.names = list(counts)
        self.total = max(1, len(counts))
        df: Counter = Counter()
        for c in counts.values():
            df.update(c.keys())
        idf = {g: math.log(1 + self.total / d) for g, d in df.items()}
        tf = {}  # 1 + ln(f) for the few distinct counts, computed once
        rows: dict[str, tuple[list, list]] = {g: ([], []) for g in df}
        sq = [0.0] * len(self.names)
        for i, name in enumerate(self.names):
            for g, f in counts[name].items():
                t = tf.get(f)
                if t is None:
                    t = tf[f] = 1 + math.log(f)
                w = t * idf[g]
                ids, ws = rows[g]
                ids.append(i)
                ws.append(w)
                sq[i] += w * w
        self.post: dict[str, bytes] = {g: array.array("I", i).tobytes() + array.array("f", w).tobytes()
                                       for g, (i, w) in rows.items()}
        self.norm = array.array("f", (math.sqrt(x) or 1.0 for x in sq))

    def _idf(self, df: int) -> float:
        return math.log(1 + self.total / df)

    def _posting(self, g: str) -> tuple[array.array, array.array]:
        raw = self.post[g]
        k = len(raw) // 8
        ids, ws = array.array("I"), array.array("f")
        ids.frombytes(raw[:4 * k])
        ws.frombytes(raw[4 * k:])
        return ids, ws

    def scores(self, query: str) -> dict[str, float]:
        """{note: cosine of the question against the note}, empty when the question has no usable grams."""
        qc = Counter(grams(query, question=True))
        qv = {}
        for g, f in qc.items():
            if g in self.post:
                qv[g] = (1 + math.log(f)) * self._idf(len(self.post[g]) // 8)
        qn = math.sqrt(sum(x * x for x in qv.values()))
        if not qn:
            return {}
        dot: dict[int, float] = {}
        for g, x in qv.items():
            ids, ws = self._posting(g)
            for i, y in zip(ids, ws):
                dot[i] = dot.get(i, 0.0) + x * y
        return {self.names[i]: s / (qn * self.norm[i]) for i, s in dot.items()}


def top(d: dict[str, float], k: int) -> list[str]:
    return [n for n, _s in sorted(d.items(), key=lambda x: -x[1])[:k]]


def fuse(scores: dict[str, float], fuzzy: dict[str, float], dense: dict[str, float] | None,
         hubs: frozenset | set = frozenset()) -> None:
    """Add the signals to `scores` in place (BM25 scale). `hubs` do not set the scale."""
    best = max([s for n, s in scores.items() if n not in hubs] or [0.0])
    m = max(best, SCALE_MIN)
    for sig, w in ((fuzzy, WEIGHT_FUZZY_WITH_DENSE if dense else WEIGHT_FUZZY), (dense or {}, WEIGHT_DENSE)):
        top_cos = max(sig.values(), default=0.0)
        if top_cos <= 0:
            continue
        for name, cos in sig.items():
            if cos > 0:
                scores[name] = scores.get(name, 0.0) + w * m * (cos / top_cos) ** POWER
