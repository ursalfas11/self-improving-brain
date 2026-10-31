"""The published detector numbers hold (eval/README.md): a pattern change that helps one language cannot
quietly cost another. No LLM, no network."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)

# loaded by path: bench/ has a run.py too
_spec = importlib.util.spec_from_file_location("eval_run", Path(__file__).resolve().parent.parent / "eval" / "run.py")
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


class TestEval(unittest.TestCase):
    def test_the_split_is_stable(self):
        self.assertEqual((len(run.load("dev")), len(run.load("test"))), (117, 133))

    def test_published_numbers_hold_on_the_test_half(self):
        m = run.measure(run.load("test"))["all"]
        self.assertGreaterEqual(m["precision"], 0.90)
        self.assertGreaterEqual(m["recall"], 0.65)
        self.assertGreaterEqual(m["durable_recall"], 0.80)

    def test_every_language_is_covered(self):
        res = run.measure(run.load("test"))
        for lang in run.LANGS:
            self.assertGreaterEqual(res[lang]["recall"], 0.5, lang)
            self.assertGreaterEqual(res[lang]["precision"], 0.8, lang)


if __name__ == "__main__":
    unittest.main()
