"""The vault gate: whose fault is a MUST finding, and does a long-lived one stay silent?

No LLM anywhere: `nightly.run` has no `llm_cmd` here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_brain import VaultCase, run_cli  # noqa: E402

from self_improving_brain import gate, learn, nightly  # noqa: E402
from self_improving_brain.lint import Finding  # noqa: E402

DEAD = "---\nname: broken\ndescription: a note with a dead link\n---\n# broken\n\nSee [[nowhere-at-all]].\n"
DEAD_TOO = "---\nname: broken-too\ndescription: another one\n---\n# broken too\n\nSee [[also-nowhere]].\n"


class TestCauses(VaultCase):
    def test_must_findings_are_sorted_into_causes(self):
        fs = [Finding("MUST", "a.md", "dead link [[x]]"), Finding("MUST", "b.md", "possible secret (token: ghp_…)"),
              Finding("MUST", "c.md", "L-x: check is not a valid regex (bad)"), Finding("MUST", "d.md", "something new"),
              Finding("SHOULD", "e.md", "dead link [[y]]"), Finding("MUST", "f.md", "dead link [[z]]")]
        self.assertEqual(gate.causes(fs), ["secret", "dead_link", "bad_regex", "other"])
        self.assertEqual(gate.causes([Finding("SHOULD", "e.md", "dead link [[y]]")]), [], "SHOULD is not red")

    def test_only_a_new_cause_belongs_to_the_steps(self):
        red = {"red": True, "causes": ["dead_link"]}
        worse = {"red": True, "causes": ["dead_link", "secret"]}
        self.assertEqual(gate.new_causes(red, red), [])
        self.assertEqual(gate.new_causes(red, worse), ["secret"])
        self.assertEqual(gate.new_causes(None, worse), ["dead_link", "secret"], "not measured before: all of it, as before")
        self.assertEqual(gate.new_causes(worse, {"red": False, "causes": []}), [])

    def test_measure_is_green_on_a_fresh_vault_and_red_with_a_dead_link(self):
        self.assertFalse(gate.measure(self.v)["red"])
        self.note("broken.md", DEAD)
        res = gate.measure(self.v)
        self.assertEqual((res["red"], res["causes"], res["count"]), (True, ["dead_link"], 1))
        self.assertIn("broken.md", res["line"])


class TestStreak(VaultCase):
    def test_one_red_night_is_quiet_two_in_a_row_are_a_finding_green_clears(self):
        red = {"red": True, "causes": ["dead_link"], "count": 1, "line": "x"}
        self.assertEqual(gate.book(self.v, red)["nights"], 1)
        self.assertEqual(gate.streak_lines(self.v), [])
        rec = gate.book(self.v, red)
        self.assertEqual((rec["nights"], rec["since"]), (2, gate.book(self.v, red)["since"]))
        line = gate.streak_lines(self.v)[0]
        self.assertIn("3 nights in a row", line)
        self.assertIn("dead_link", line)
        self.assertIn("fix or remove the link", line)
        self.assertIsNone(gate.book(self.v, {"red": False, "causes": [], "count": 0, "line": ""}))
        self.assertEqual(gate.streak_lines(self.v), [])


class TestNightly(VaultCase):
    def test_a_red_that_was_there_before_is_not_the_nights_and_is_said_after_two_nights(self):
        self.note("broken.md", DEAD)
        rep = nightly.run(self.v)
        self.assertEqual(rep["steps"]["lint"]["gate"], {"causes": ["dead_link"], "new": [], "nights": 1})
        self.assertEqual([x for x in rep["attention"] if "MUST" in x], [], "it was red before the steps: not theirs")
        self.assertNotIn("MUST findings", run_cli())
        rep = nightly.run(self.v)
        self.assertEqual(rep["steps"]["lint"]["gate"]["nights"], 2)
        for out in (run_cli(), run_cli("doctor"), run_cli("review", "--no-input")):
            self.assertIn("MUST findings for 2 nights in a row", out)
        (self.root / "broken.md").unlink()
        nightly.run(self.v)
        self.assertNotIn("MUST findings", run_cli())

    def test_a_cause_that_came_with_the_steps_is_named(self):
        real = learn.scan

        def scan_and_break(v, *a, **kw):
            (v.root / "broken-too.md").write_text(DEAD_TOO, encoding="utf-8")
            return real(v, *a, **kw)

        with mock.patch.object(learn, "scan", scan_and_break):
            rep = nightly.run(self.v)
        self.assertEqual(rep["steps"]["lint"]["gate"]["new"], ["dead_link"])
        self.assertTrue(any("left a MUST finding" in x and "dead_link" in x for x in rep["attention"]), rep["attention"])

    def test_a_quick_run_does_not_count_a_night(self):
        self.note("broken.md", DEAD)
        nightly.run(self.v, quick=True)
        nightly.run(self.v, quick=True)
        self.assertEqual(gate.streak_lines(self.v), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
