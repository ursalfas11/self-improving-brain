"""Fuzzy and dense recall: word forms and typos are found, the signals fuse on the BM25 scale, a dead or foreign
embedding server never breaks a search, and the published recall numbers hold. No network, no ollama."""
from __future__ import annotations

import array
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from self_improving_brain import cache, dense, fuzzy  # noqa: E402
from self_improving_brain.config import Vault  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.search import Index  # noqa: E402

_spec = importlib.util.spec_from_file_location("eval_recall", Path(__file__).resolve().parent.parent / "eval" / "recall.py")
recall = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recall)


def make_vault(root: Path, notes: dict) -> Library:
    for name, (desc, body) in notes.items():
        (root / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n\n## Body\n{body}\n",
                                         encoding="utf-8")
    return Library(root)


NOTES = {
    "image-storage": ("Product images and cartons", "Cartonbilder live in the bucket, named by article number."),
    "cash-book": ("Month end in the cash book", "Close the month, sort the receipts, export the totals."),
    "server-move": ("Moving to new servers", "Plan the move at night and test after the switch."),
}


class TestFuzzy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = make_vault(Path(self.tmp.name), NOTES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_grams_mark_word_boundaries_and_drop_filler(self):
        g = fuzzy.grams("hey please fix cartonbild", question=True)
        self.assertIn("_car", g)
        self.assertIn("ild_", g)
        self.assertFalse(any("hey" in x or "fix" in x for x in g))
        self.assertEqual(fuzzy.grams("ab"), [])
        self.assertEqual(fuzzy.grams("abcdef"), ["_abc", "abcd", "bcde", "cdef", "def_"])

    def test_a_word_form_that_bm25_misses_is_found(self):
        idx = Index(self.lib, {})
        self.assertEqual(idx.score("cartonbild", fuzzy=False), [])
        hits = idx.score("cartonbild", fuzzy=True)
        self.assertEqual(hits[0].note.name, "image-storage")
        self.assertEqual(hits[0].why, "fuzzy")

    def test_a_typo_is_found(self):
        hits = Index(self.lib, {}).score("recipts monthend", fuzzy=True)
        self.assertEqual(hits[0].note.name, "cash-book")

    def test_without_any_gram_in_common_nothing_changes(self):
        idx = Index(self.lib, {})
        self.assertEqual([h.note.name for h in idx.score("zzzzqqqq", fuzzy=True)], [])

    def test_a_word_hit_stays_on_top_and_the_scale_is_bm25(self):
        idx = Index(self.lib, {})
        plain = idx.score("servers move", fuzzy=False)
        fused = idx.score("servers move", fuzzy=True)
        self.assertEqual(fused[0].note.name, plain[0].note.name)
        self.assertGreaterEqual(fused[0].score, plain[0].score)
        # no signal adds more than its weight times the best word score
        self.assertLessEqual(fused[0].score, plain[0].score + fuzzy.WEIGHT_FUZZY * max(plain[0].score, fuzzy.SCALE_MIN) + 0.01)

    def test_a_fuzzy_only_hit_never_reaches_the_default_threshold(self):
        top = Index(self.lib, {}).score("cartonbild", fuzzy=True)[0]
        self.assertLess(top.score, 4.0)  # recall_threshold default: the signal orders, the words decide to show

    def test_hubs_are_not_indexed(self):
        (Path(self.tmp.name) / "hub.md").write_text("---\nname: hub\ntype: hub\ndescription: all\n---\nCartonbilder Cartonbilder\n")
        lib = Library(Path(self.tmp.name))
        self.assertNotIn("hub", fuzzy.FuzzyIndex(lib).names)

    def test_a_broken_signal_leaves_the_word_search_alone(self):
        idx = Index(self.lib, {})
        with mock.patch.object(fuzzy.FuzzyIndex, "scores", side_effect=RuntimeError("boom")):
            self.assertEqual([h.note.name for h in idx.score("servers move", fuzzy=True)],
                             [h.note.name for h in idx.score("servers move", fuzzy=False)])

    def test_the_index_survives_pickling(self):
        import pickle
        idx = pickle.loads(pickle.dumps(Index(self.lib, {})))
        self.assertEqual(idx.score("cartonbild")[0].note.name, "image-storage")

    def test_fuse_orders_by_cosine_with_a_steep_falloff(self):
        scores = {"a": 10.0}
        fuzzy.fuse(scores, {"a": 0.5, "b": 0.5, "c": 0.25}, None)
        self.assertGreater(scores["b"], 3 * scores["c"])  # (1)^4 against (0.5)^4 = 16x


class TestDense(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "notes").mkdir()
        self.lib = make_vault(root / "notes", NOTES)
        self.v = Vault(root / "vault", {})
        (root / "vault" / ".brain").mkdir(parents=True)
        os.environ.pop("OLLAMA_HOST", None)
        dense._mem[:] = [None, None]

    def tearDown(self):
        os.environ.pop("OLLAMA_HOST", None)
        self.tmp.cleanup()

    def test_only_this_machine_is_ever_contacted(self):
        for host, want in (("", ("127.0.0.1", 11434)), ("127.0.0.1:9", ("127.0.0.1", 9)), ("http://localhost:11434/", ("localhost", 11434)),
                           ("0.0.0.0:11434", ("127.0.0.1", 11434)), ("[::1]:11434", ("::1", 11434)),
                           ("gpu-box.example:11434", None), ("10.0.0.5", None), ("https://api.example.com", None)):
            os.environ["OLLAMA_HOST"] = host
            self.assertEqual(dense.endpoint(), want, host)

    def test_a_foreign_host_makes_no_connection_at_all(self):
        os.environ["OLLAMA_HOST"] = "gpu-box.example:11434"
        with mock.patch("http.client.HTTPConnection") as conn:
            self.assertFalse(dense.model_available())
            self.assertIsNone(dense._call("POST", "/api/embed", {}, 1))
            self.assertIn("not this machine", dense.build(self.v, self.lib))
        conn.assert_not_called()

    def _store(self, vecs: dict):
        docs = {n: (fuzzy.note_sig(self.lib.notes[n]), array.array("f", v).tobytes(), []) for n, v in vecs.items()}
        cache._store(self.v, "dense", dense._FP, docs)

    def test_vectors_order_the_notes_and_a_failure_pauses_the_signal(self):
        self._store({"image-storage": (1, 0, 0), "cash-book": (0, 1, 0), "server-move": (0, 0, 1)})
        with mock.patch.object(dense, "embed", return_value=[array.array("f", (0, 1, 0))]):
            s = dense.scores(self.v, self.lib, "anything")
        self.assertEqual(max(s, key=s.get), "cash-book")
        with mock.patch.object(dense, "embed", side_effect=OSError("down")) as e:
            self.assertEqual(dense.scores(self.v, self.lib, "anything"), {})
            self.assertEqual(dense.scores(self.v, self.lib, "anything"), {})
        self.assertEqual(e.call_count, 1)  # the second call did not even try: five minutes of rest

    def test_without_a_vector_file_there_is_no_signal(self):
        with mock.patch.object(dense, "embed") as e:
            self.assertEqual(dense.scores(self.v, self.lib, "anything"), {})
        e.assert_not_called()

    def test_build_is_off_without_the_model_and_reuses_unchanged_notes(self):
        with mock.patch.object(dense, "model_available", return_value=False):
            self.assertIn("off", dense.build(self.v, self.lib))
        calls = []

        def fake(texts, deadline):
            calls.append(len(texts))
            return [array.array("f", (1.0, 0.0)) for _ in texts]
        with mock.patch.object(dense, "model_available", return_value=True), mock.patch.object(dense, "embed", fake):
            self.assertEqual(dense.build(self.v, self.lib)["new"], 3)
            self.assertEqual(dense.build(self.v, self.lib)["new"], 0)  # nothing changed: no call
        self.assertEqual(len(calls), 3)

    def test_dense_scores_fuse_into_the_ranking(self):
        self._store({"image-storage": (0, 0, 1), "cash-book": (0, 1, 0), "server-move": (1, 0, 0)})
        idx = Index(self.lib, {})
        with mock.patch.object(dense, "embed", return_value=[array.array("f", (1, 0, 0))]):
            hits = idx.score("something with no shared word at all xx", dense=self.v)
        self.assertEqual(hits[0].note.name, "server-move")


class TestDenseProtocol(unittest.TestCase):
    """The real HTTP path against a tiny stand-in for ollama on the loopback address (no outside network)."""

    def test_tags_and_embed_round_trip(self):
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"models": [{"name": "bge-m3:latest"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._send({"embeddings": [[3.0, 4.0] for _ in body["input"]]})

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["OLLAMA_HOST"] = f"127.0.0.1:{srv.server_port}"
        try:
            self.assertTrue(dense.model_available())
            vecs = dense.embed(["a", "b"], 2.0)
            self.assertEqual([round(x, 2) for x in vecs[0]], [0.6, 0.8])  # unit length
        finally:
            os.environ.pop("OLLAMA_HOST", None)
            srv.shutdown()
            srv.server_close()


class TestPublishedNumbers(unittest.TestCase):
    """eval/README.md: the fuzzy signal helps on the test half, and does not cost the exact questions."""

    @classmethod
    def setUpClass(cls):
        cls.idx = Index(Library(recall.VAULT), {})
        cls.rows = recall.load(recall.GOLD, "test")

    def test_the_split_is_stable(self):
        self.assertEqual((len(recall.load(recall.GOLD, "dev")), len(recall.load(recall.GOLD, "test"))), (60, 63))

    def test_fuzzy_beats_words_on_the_test_half(self):
        words = recall.measure(self.idx, self.rows, False)["by"]["all"]
        both = recall.measure(self.idx, self.rows, True)["by"]["all"]
        self.assertGreaterEqual(both["top1"], 95)
        self.assertGreaterEqual(both["top5"], 95)
        self.assertGreater(both["top1"], words["top1"] + 6)

    def test_exact_questions_do_not_get_worse(self):
        words = recall.measure(self.idx, self.rows, False)["by"]["exact"]
        both = recall.measure(self.idx, self.rows, True)["by"]["exact"]
        self.assertGreaterEqual(both["top1"], words["top1"])
        self.assertGreaterEqual(both["top5"], words["top5"])

    def test_the_default_threshold_shows_more_right_notes_and_not_more_wrong_ones(self):
        w = recall.measure(self.idx, self.rows, False, (4.0,))["threshold"][4.0]
        f = recall.measure(self.idx, self.rows, True, (4.0,))["threshold"][4.0]
        self.assertGreater(f["right"], w["right"])
        self.assertLessEqual(f["wrong"], w["wrong"])

    def test_every_gold_note_exists(self):
        names = set(self.idx.lib.notes)
        for r in recall.load(recall.GOLD, "all"):
            self.assertTrue(set(r["gold"]) <= names, r["id"])


if __name__ == "__main__":
    unittest.main()
