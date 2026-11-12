"""Self-care: upkeep findings are fixed at night, checked, journaled, and never fixed again once you took them back.

No test here calls a real LLM: `llm.ask_json` is mocked.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_brain import VaultCase, run_cli  # noqa: E402

from self_improving_brain import care, journal, lint, llm, nightly  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402

LONG = ("Before every deploy to production run `make migrate-check` and read its output, because in March the "
        "migration 0042 locked the orders table for eleven minutes and nobody had looked at the plan before, and "
        "after that we agreed that the check is cheap and the outage was not, so do it every single time.")


class TestCare(VaultCase):
    def should(self):
        return [str(f) for f in lint.lint(self.v) if f.level == "SHOULD"]

    def test_description_orphan_and_trigger_are_fixed_without_an_llm(self):
        p = self.note("20-areas/backups.md", "---\nname: backups\n---\n# Backups\n\n"
                      "- Nightly dumps of the orders database go to the bucket acme-backups. Kept for 30 days.\n")
        q = self.note("20-areas/style.md", "---\nname: style\ndescription: How code is formatted here\n---\n# Style\n\n"
                      "> [!lesson]- L-format\n> rule: Run the formatter on every changed file before committing.\n"
                      "> mechanism: reflex\n\nPart of [[hub-brain]].\n")
        self.assertEqual(len([f for f in self.should() if "backups" in f or "L-format" in f]), 3)
        rep = care.run(self.v)
        self.assertEqual(sorted(x.split()[0] for x in rep["fixed"]), ["description", "orphan", "trigger"])
        n = Library(self.root).find("backups")
        self.assertEqual(n.description, "Nightly dumps of the orders database go to the bucket acme-backups.")
        self.assertEqual(n.links, ["hub-brain"])
        self.assertIn("[[backups]]", (self.root / Library(self.root).find("hub-brain").rel).read_text())
        l = next(x for x in Library(self.root).lessons() if x.id == "L-format")
        self.assertEqual(l.words[:2], ["formatter", "changed"])
        self.assertFalse([f for f in self.should() if "backups" in f or "L-format" in f])
        self.assertEqual(care.run(self.v)["fixed"], [], "a second night finds nothing to do")
        # your veto holds: what you took back is not fixed again
        for e in [e for e in journal.recent(self.v) if e["kind"] == "care"]:
            journal.undo(self.v, e["id"])
        back = p.read_text()
        self.assertTrue(back.endswith("# Backups\n\n- Nightly dumps of the orders database go to the bucket "
                                      "acme-backups. Kept for 30 days.\n"), back)
        self.assertNotIn("description:", back)
        self.assertNotIn("words:", q.read_text())
        self.assertNotIn("[[backups]]", (self.root / Library(self.root).find("hub-brain").rel).read_text())
        self.assertEqual(care.run(self.v)["fixed"], [])

    def test_a_long_rule_is_shortened_only_when_nothing_gets_lost(self):
        self.note("20-areas/deploy.md", "---\nname: deploy\ndescription: How we deploy\n---\n# Deploy\n\n"
                  f"> [!lesson]- L-migrate\n> rule: {LONG}\n> mechanism: reflex\n> words: deploy, production\n\n"
                  "Part of [[hub-brain]].\n")
        self.v.cfg["llm_cmd"] = "x"
        with mock.patch.object(llm, "ask_json", lambda v, p: {"rule": "Check migrations before every deploy."}):
            rep = care.run(self.v, think=True)
        self.assertEqual(rep["fixed"], [], "it lost the command: refused")
        self.assertEqual(next(l for l in Library(self.root).lessons() if l.id == "L-migrate").rule, LONG)
        good = "Before every deploy to production run `make migrate-check` and read its output (migration 0042)."
        with mock.patch.object(llm, "ask_json", lambda v, p: {"rule": good}):
            rep = care.run(self.v, think=True)
        self.assertEqual(rep["fixed"], ["shorten L-migrate"])
        e = journal.recent(self.v)[0]
        self.assertEqual((e["what"], e["before"], e["after"]), ("shorten", LONG, good))
        journal.undo(self.v, e["id"])
        self.assertEqual(next(l for l in Library(self.root).lessons() if l.id == "L-migrate").rule, LONG)
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("not asked again")):
            self.assertEqual(care.run(self.v, think=True)["fixed"], [])

    def twins(self):
        a = self.note("20-areas/staging-database.md", "---\nname: staging-database\ndescription: Where the staging "
                      "database of the shop lives\n---\n# Staging database of the shop\n\n"
                      "The staging database runs in eu-west-2 since October.\n\nPart of [[hub-brain]].\n")
        b = self.note("20-areas/staging-db.md", "---\nname: staging-db\ndescription: Where the staging database of "
                      "the shop lives now\n---\n# Staging database of the shop\n\n"
                      "Staging DB: eu-west-2, port 5433, see `infra/staging.tf`.\n\nPart of [[hub-brain]].\n")
        self.v.cfg["llm_cmd"] = "x"
        return a, b

    def test_two_notes_that_say_the_same_become_one(self):
        a, b = self.twins()
        before = (a.read_text(), b.read_text())
        self.assertTrue([f for f in self.should() if "duplicate" in f])
        lossy = {"verdict": "same", "keep": "staging-database", "reason": "same database",
                 "body": "# Staging database of the shop\n\nThe staging database runs in eu-west-2 since October.\n\n"
                         "Part of [[hub-brain]].\n"}
        with mock.patch.object(llm, "ask_json", lambda v, p: lossy):
            rep = care.run(self.v, think=True)
        self.assertEqual((rep["fixed"], a.read_text(), b.read_text()), ([], *before), "it lost the port: nothing written")
        whole = dict(lossy, body="# Staging database of the shop\n\nThe staging database runs in eu-west-2 since "
                                 "October, port 5433, see `infra/staging.tf`.\n\nPart of [[hub-brain]].\n")
        with mock.patch.object(llm, "ask_json", lambda v, p: whole):
            rep = care.run(self.v, think=True)
        self.assertEqual(rep["fixed"], ["merged staging-db into staging-database"])
        self.assertIn("port 5433", a.read_text())
        self.assertIn("Merged into [[staging-database]]", b.read_text())
        self.assertFalse([f for f in lint.lint(self.v) if f.level == "MUST" or "duplicate" in str(f)])
        self.assertIn("merged", run_cli("review"))
        self.assertIn("two notes again", journal.undo(self.v, journal.recent(self.v)[0]["id"]))
        self.assertEqual((a.read_text(), b.read_text()), before)
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("not merged a second time")):
            care.run(self.v, think=True)
        self.assertFalse([f for f in self.should() if "duplicate" in f], "and not listed again")

    def test_look_alikes_that_differ_are_remembered(self):
        self.twins()
        with mock.patch.object(llm, "ask_json", lambda v, p: {"verdict": "different", "reason": "two databases"}):
            rep = care.run(self.v, think=True)
        self.assertEqual((rep["fixed"], rep["distinct"]), ([], 1))
        self.assertFalse([f for f in self.should() if "duplicate" in f])
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("not asked again")):
            self.assertEqual(care.run(self.v, think=True)["distinct"], 0)

    def test_a_finding_that_fails_twice_rests(self):
        self.twins()
        for _ in range(care.TRIES_MAX):
            with mock.patch.object(llm, "ask_json", lambda v, p: {"verdict": "same", "keep": "elsewhere", "body": "x"}):
                care.run(self.v, think=True)
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("it rests")):
            self.assertEqual(care.run(self.v, think=True)["resting"], 1)

    def test_a_note_you_are_working_on_is_left_alone(self):
        import subprocess
        git = lambda *a: subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, text=True)  # noqa: E731
        git("init", "-q")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "start")
        self.note("20-areas/backups.md", "---\nname: backups\n---\n# Backups\n\n"
                  "Nightly dumps of the orders database go to the bucket acme-backups.\n")
        self.assertEqual(care.run(self.v)["fixed"], [], "not committed yet: you may still be writing it")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "note")
        self.assertEqual(len(care.run(self.v)["fixed"]), 2)

    def test_the_night_tidies_and_says_so(self):
        self.note("20-areas/backups.md", "---\nname: backups\n---\n# Backups\n\n"
                  "Nightly dumps of the orders database go to the bucket acme-backups.\n")
        with mock.patch.object(llm, "available", return_value=False):
            rep = nightly.run(self.v, quick=True)
        self.assertEqual(len(rep["steps"]["care"]["fixed"]), 2)
        self.assertEqual(rep["attention"], [])
        self.assertIn("2 tidied", nightly.summary(rep))
        self.assertIn("tidied", run_cli("review"))


if __name__ == "__main__":
    unittest.main()
