"""One topic, one state: notes that say the same are found by content, a new note that doubles a topic is refused,
`brain remember` says where a claim goes, and two rules that demand the same become one (undoable).

No test here calls a real LLM: `llm.ask_json` is mocked or the judgement is passed in.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_brain import VaultCase, run_cli  # noqa: E402

from self_improving_brain import care, distill, inbox, journal, learn, llm, nightly, themes  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.util import today  # noqa: E402

STAGING = ("The staging database of the shop runs on the managed cluster in eu-west-2. Nightly dumps of the staging "
           "database go to the bucket acme-staging-dumps and stay for 14 days. The staging database is reset every "
           "Monday from the latest production dump, so test orders do not survive the weekend.")


def days_ago(n: int) -> str:
    return (date.fromisoformat(today()) - timedelta(days=n)).isoformat()


def lesson(id_: str, rule: str, words: str, extra: str = "", mech: str = "reflex", since: str = "") -> str:
    since = since or days_ago(20)
    return (f"> [!lesson]- {id_}\n> rule: {rule}\n> mechanism: {mech}\n> words: {words}\n{extra}> since: {since}\n"
            f"> evidence: said twice\n")


class TestNotesByContent(VaultCase):
    def twins(self):
        a = self.note("20-areas/staging-database.md", "---\nname: staging-database\ndescription: Staging database of the shop\n"
                      f"---\n# Staging database\n\n{STAGING}\n\nPart of [[hub-brain]].\n")
        b = self.note("20-areas/eu-west-cluster.md", "---\nname: eu-west-cluster\ndescription: What runs in the eu-west-2 region\n"
                      f"---\n# Cluster in eu-west-2\n\n{STAGING} The reset script lives in `infra/reset.sh`.\n\nPart of [[hub-brain]].\n")
        return a, b

    def test_notes_with_other_titles_but_the_same_content_are_found(self):
        self.twins()
        from self_improving_brain.lint import duplicate_pairs
        lib = Library(self.root)
        self.assertEqual(duplicate_pairs(lib), [], "title and description do not look alike")
        self.assertEqual([(a, b) for a, b, _s in themes.topic_pairs(lib)], [("eu-west-cluster", "staging-database")])

    def test_what_stands_side_by_side_on_purpose_is_never_a_pair(self):
        self.twins()
        self.note("20-areas/staging-database-2026-10.md", "---\nname: staging-database-2026-10\ndescription: Analysis\n"
                  f"---\n# Staging analysis\n\n{STAGING}\n")
        self.note("20-areas/staging-part.md", "---\nname: staging-part\npart_of: staging-database\n---\n# Part\n\n"
                  f"{STAGING}\n")
        self.note("20-areas/old-staging.md", "---\nname: old-staging\ndescription: Merged into staging-database.\n---\n"
                  f"# Old\n\n{STAGING}\n")
        self.note("00-hubs/hub-staging.md", f"---\nname: hub-staging\ntype: hub\ndescription: Hub\n---\n# Hub\n\n{STAGING}\n")
        names = {n for a, b, _s in themes.topic_pairs(Library(self.root)) for n in (a, b)}
        self.assertEqual(names, {"eu-west-cluster", "staging-database"})

    def test_care_judges_and_merges_them_like_any_look_alike(self):
        a, b = self.twins()
        self.v.cfg["llm_cmd"] = "x"
        merged = {"verdict": "same", "keep": "staging-database", "reason": "one database",
                  "body": f"# Staging database\n\n{STAGING} The reset script lives in `infra/reset.sh`.\n\nPart of [[hub-brain]].\n"}
        with mock.patch.object(llm, "ask_json", lambda v, p: merged):
            rep = care.run(self.v, think=True)
        self.assertEqual(rep["fixed"], ["merged eu-west-cluster into staging-database"])
        self.assertIn("infra/reset.sh", a.read_text())
        self.assertIn("Merged into [[staging-database]]", b.read_text())
        self.assertEqual(themes.topic_pairs(Library(self.root)), [], "the pointer is not compared again")
        self.assertIn("two notes again", journal.undo(self.v, journal.recent(self.v)[0]["id"]))
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("not asked again")):
            care.run(self.v, think=True)

    def test_brain_themes_lists_pairs_without_an_llm(self):
        self.twins()
        out = run_cli("themes")
        self.assertIn("eu-west-cluster ↔ staging-database", out)
        self.assertIn("0 rule pairs", out)


class TestBeforeWriting(VaultCase):
    def setUp(self):
        super().setUp()
        self.note("20-areas/staging-database.md", "---\nname: staging-database\ndescription: Staging database of the shop\n"
                  f"---\n# Staging database\n\n{STAGING}\n\nPart of [[hub-brain]].\n")
        self.note("20-areas/invoices.md", "---\nname: invoices\ndescription: Invoice numbering\n---\n# Invoices\n\n"
                  "Invoices are numbered per year and start at one in January.\n\nPart of [[hub-brain]].\n")
        self.v.cfg["llm_cmd"] = "x"

    def test_a_new_note_on_a_topic_you_have_is_refused_and_the_next_try_is_told_where(self):
        it = inbox.add(self.v, "fact", "The staging database of the shop is reset every Monday from the production dump.")
        prompts = []

        def answer(v, prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"action": "new", "title": "Staging database reset", "description": "Weekly reset of the staging database",
                        "text": "The staging database of the shop is reset every Monday from the latest production dump."}
            return {"action": "append", "note": "staging-database", "text": "- Reset: Mondays, from the production dump."}
        with mock.patch.object(llm, "ask_json", answer):
            first = distill.auto(self.v)
            self.assertIn("refused a second note on one topic", first[0])
            self.assertFalse((self.root / "20-areas/staging-database-reset.md").exists())
            second = distill.auto(self.v)
        self.assertIn("append", second[0])
        self.assertIn("[[staging-database]]", prompts[1], "the retry says where the topic stands")
        self.assertIn("Mondays", (self.root / "20-areas/staging-database.md").read_text())
        self.assertEqual(inbox.items(self.v), [], it["id"] + " is filed")

    def test_a_new_topic_is_not_refused(self):
        self.assertEqual(themes.existing_topic(Library(self.root), "Rate limits", "How the public API throttles clients",
                                               "The public API allows 60 requests per minute per key and answers 429."), "")
        self.assertIn("[[staging-database]]", themes.existing_topic(
            Library(self.root), "Staging database reset", "Weekly reset of the staging database", STAGING))

    def test_the_distill_prompt_names_notes_that_may_carry_the_topic(self):
        inbox.add(self.v, "fact", "Staging database dumps go to acme-staging-dumps and stay for fourteen days.")
        seen = []
        with mock.patch.object(llm, "ask_json", lambda v, p: seen.append(p) or {"action": "skip", "reason": "known"}), \
                mock.patch.object(distill, "candidates", lambda lib, claim, k=3: []):
            distill.auto(self.v)
        self.assertTrue(seen)
        self.assertIn("may carry this topic already", " ".join(seen))

    def test_remember_says_where_the_claim_will_go(self):
        out = run_cli("remember", "fact", "The staging database of the shop is reset every Monday at four in the morning.")
        self.assertIn("in inbox", out)
        self.assertIn("already stands in [[staging-database]]", out)
        quiet = run_cli("remember", "fact", "Webhooks of the payment provider are retried for three days.")
        self.assertNotIn("already stands", quiet)


class TestJoiningRules(VaultCase):
    def pair(self, second_mech="reflex", cmd_b=""):
        self.note("20-areas/ci.md", "---\nname: ci\ndescription: CI habits\n---\n# CI\n\n"
                  + lesson("L-lint-first", "Run `make lint` before every pull request.", "lint, pull request",
                           "> commands: ^gh pr create\n") + "\nPart of [[hub-brain]].\n")
        self.note("20-areas/review.md", "---\nname: review\ndescription: Review habits\n---\n# Review\n\n"
                  + lesson("L-lint-before-pr", "Before opening a pull request run `make lint` and fix what it reports.",
                           "linter, pull request, review", cmd_b, mech=second_mech, since=days_ago(10))
                  + "\nPart of [[hub-brain]].\n")
        self.note("20-areas/other.md", "---\nname: other\ndescription: Other\n---\n# Other\n\n"
                  + lesson("L-tag", "Tag every release before deploying to production.", "release, tag")
                  + "\nPart of [[hub-brain]].\n")

    JOINED = "Before every pull request run `make lint` and fix what it reports."

    def judge(self, verdict="same", rule=JOINED):
        return lambda todo: {i: {"verdict": verdict, "rule": rule, "reason": "the same demand"} for i in range(1, len(todo) + 1)}

    def active(self):
        return {l.id: l for l in Library(self.root).lessons()}

    def test_the_older_rule_carries_both_and_the_other_points_to_it(self):
        self.pair(second_mech="always")
        rep = themes.join_lessons(self.v, judge=self.judge())
        self.assertEqual((rep["joined"], rep["refused"]), (["L-lint-first"], []))
        act = self.active()
        self.assertNotIn("L-lint-before-pr", act)
        self.assertIn("L-tag", act)
        win = act["L-lint-first"]
        self.assertEqual((win.rule, win.mechanism, win.commands), (self.JOINED, "always", "^gh pr create"))
        self.assertEqual(win.words, ["lint", "pull request", "linter", "review"])
        gone = next(l for l in Library(self.root).lessons(retired=True) if l.id == "L-lint-before-pr")
        self.assertIn("joined into L-lint-first", gone.retired)
        e = journal.recent(self.v)[0]
        self.assertEqual((e["kind"], e["lesson"], e["joined"]), ("join", "L-lint-first", "L-lint-before-pr"))
        self.assertIn("joined", run_cli("review"))

    def test_undo_restores_both_and_the_pair_is_never_joined_again(self):
        self.pair()
        before = {p.name: p.read_text() for p in self.root.glob("20-areas/*.md")}
        themes.join_lessons(self.v, judge=self.judge())
        self.assertIn("two rules again", journal.undo(self.v, journal.recent(self.v)[0]["id"]))
        act = self.active()
        self.assertEqual((act["L-lint-first"].rule, act["L-lint-before-pr"].mechanism),
                         ("Run `make lint` before every pull request.", "reflex"))
        self.assertEqual(act["L-lint-first"].words, ["lint", "pull request"])
        self.assertEqual(themes.join_lessons(self.v, judge=lambda t: self.fail("asked again"))["judged"], 0)
        self.assertEqual({p.name: p.read_text().count("L-lint") for p in self.root.glob("20-areas/*.md")},
                         {n: t.count("L-lint") for n, t in before.items()})

    def test_nothing_that_a_rule_names_may_get_lost(self):
        self.pair()
        n = len(self.active())
        rep = themes.join_lessons(self.v, judge=self.judge(rule="Lint before every pull request and fix what it reports."))
        self.assertEqual(rep["joined"], [])
        self.assertIn("lost `make lint`", rep["refused"][0][1])
        rep = themes.join_lessons(self.v, judge=self.judge(rule="See https://example.com/lint before every pull request `make lint`."))
        self.assertEqual(rep["joined"], [])
        self.assertEqual(len(self.active()), n, "nothing was written")

    def test_rules_with_different_triggers_and_guards_stay_as_they_are(self):
        self.pair(cmd_b="> commands: ^git push\n")
        rep = themes.join_lessons(self.v, judge=self.judge())
        self.assertEqual(rep["joined"], [])
        self.assertIn("different commands", rep["refused"][0][1])
        self.pair(second_mech="guard")
        rep = themes.join_lessons(self.v, judge=lambda t: self.fail("a guard is never compared"))
        self.assertEqual(rep["candidates"], 0)

    def test_different_is_remembered_until_a_rule_changes(self):
        self.pair()
        self.assertEqual(themes.join_lessons(self.v, judge=self.judge("different"))["joined"], [])
        self.assertEqual(themes.join_lessons(self.v, judge=lambda t: self.fail("asked again"))["judged"], 0)
        learn.set_lesson_field(self.v, "L-lint-first", "rule", "Run `make lint` before every single pull request.")
        self.assertEqual(themes.join_lessons(self.v, judge=self.judge("different"))["judged"], 1)

    def test_at_most_two_rules_a_night_and_one_call(self):
        notes = []
        for i, topic in enumerate(("lint", "format", "typecheck")):
            body = (lesson(f"L-{topic}-a", f"Run `make {topic}` before every pull request.", f"{topic}, pull request")
                    + lesson(f"L-{topic}-b", f"Before a pull request run `make {topic}` and fix what it reports.",
                             f"{topic}, review", since=days_ago(30)))
            notes.append(self.note(f"20-areas/n{i}.md", f"---\nname: n{i}\ndescription: N{i}\n---\n# N{i}\n\n{body}\nPart of [[hub-brain]].\n"))
        calls = []

        def judge(todo):
            calls.append(len(todo))
            return {i: {"verdict": "same", "reason": "same",
                        "rule": f"Before every pull request run `make {a.words[0]}` and fix what it reports."}
                    for i, (_w, a, _b) in enumerate(todo, 1)}
        rep = themes.join_lessons(self.v, judge=judge)
        self.assertEqual((len(calls), len(rep["joined"])), (1, themes.JOIN_MAX))

    def test_the_night_joins_with_one_call_and_says_so(self):
        self.pair()
        self.v.cfg["llm_cmd"] = "x"
        prompts = []

        def fake(v, prompt):
            if "pairs of working rules" in prompt:
                prompts.append(prompt)
                return [{"pair": 1, "verdict": "same", "rule": self.JOINED, "reason": "the same demand"}]
            return {}
        with mock.patch.object(llm, "ask_json", fake):
            rep = nightly.run(self.v)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(rep["steps"]["themes"]["joined"], ["L-lint-first"])
        self.assertIn("1 rule joined", nightly.summary(rep))
        self.assertFalse([k for k, x in rep["steps"].items() if str(x).startswith("error")], rep["steps"])

    def test_without_an_llm_the_step_only_counts(self):
        self.pair()
        self.assertEqual(themes.run(self.v, think=False), {"candidates": 1, "joined": []})


if __name__ == "__main__":
    unittest.main()
