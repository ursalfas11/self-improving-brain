"""The answer check, the merge of drafts by meaning, the budget per purpose, parking, and splitting large notes.

No test here calls a real LLM: `llm.ask_json` / `llm._ask` are mocked.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_autonomy import _ago, held  # noqa: E402
from test_brain import VaultCase, run_cli, write_claude  # noqa: E402

from self_improving_brain import answers, care, hooks, journal, learn, lint, llm, nightly  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.util import append_jsonl, read_jsonl, save_json  # noqa: E402

ANSWER = ("I changed the deploy script, ran the whole test suite and pushed the branch. Everything is green now and "
          "the release can go out tomorrow morning as planned, nothing else needs your attention today.")


class TestAnswerCheck(VaultCase):
    def setUp(self):
        super().setUp()
        self.v.cfg["llm_cmd"] = "x"
        self.tp = Path(os.environ["HOME"]) / ".claude/projects/p/s1.jsonl"
        write_claude(self.tp, "s1", [
            ("user", "fix the deploy script"),
            ("assistant", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "make deploy-test"}}]),
            ("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]),
            ("assistant", [{"type": "text", "text": ANSWER}])])

    def verdict(self, *breaches):
        return {"checked": ["L-result-first", "L-verify-before-done"], "breaches": list(breaches)}

    def test_a_breach_counts_only_with_a_verbatim_quote(self):
        real = {"rule": "L-verify-before-done", "quote": "Everything is green now", "better": "Show the test output."}
        made_up = {"rule": "L-result-first", "quote": "Here is a long preamble about nothing", "better": "Be short."}
        with mock.patch.object(llm, "ask_json", lambda v, p: self.verdict(real, made_up)):
            r = answers.check(self.v, self.tp, "s1-session")
        self.assertEqual([b["rule"] for b in r["breaches"]], ["L-verify-before-done"], "an invented quote is dropped")
        rec = read_jsonl(self.v.path("state", "answers.jsonl"))[-1]
        self.assertEqual((rec["checked"], rec["broken"]), (["L-result-first", "L-verify-before-done"],
                                                           ["L-verify-before-done"]))
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("checked once")):
            self.assertEqual(answers.check(self.v, self.tp, "s1-session"), {"skipped": "checked already"})

    def test_the_breach_is_shown_once_in_the_next_prompt(self):
        real = {"rule": "L-verify-before-done", "quote": "Everything is green now", "better": "Show the test output."}
        with mock.patch.object(llm, "ask_json", lambda v, p: self.verdict(real)):
            answers.check(self.v, self.tp, "s1-session")
        out = hooks.prompt(self.v, {"session_id": "s1-session", "prompt": "now update the changelog"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("BRAIN-ANSWER-CHECK", ctx)
        self.assertIn("“Everything is green now” → Show the test output", ctx)
        again = hooks.prompt(self.v, {"session_id": "s1-session", "prompt": "now update the changelog"})
        self.assertNotIn("BRAIN-ANSWER-CHECK", json.dumps(again), "exactly once")
        self.assertEqual(answers.feedback(self.v, "other-session"), "", "never in another session")

    def test_off_short_or_without_llm_nothing_is_sent(self):
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("no call")):
            self.v.cfg["answer_check"] = False
            self.assertEqual(answers.check(self.v, self.tp), {"skipped": "off"})
            self.v.cfg["answer_check"] = True
            self.v.cfg["llm_cmd"] = ""
            self.assertEqual(answers.check(self.v, self.tp), {"skipped": "no llm_cmd"})
            self.v.cfg["llm_cmd"] = "x"
            short_tp = Path(os.environ["HOME"]) / ".claude/projects/p/s2.jsonl"
            write_claude(short_tp, "s2", [("user", "and?"), ("assistant", [{"type": "text", "text": "Done."}])])
            self.assertEqual(answers.check(self.v, short_tp), {"skipped": "short"})

    def test_the_stop_hook_starts_the_check_detached(self):
        with mock.patch.object(hooks, "_background") as bg:
            self.assertEqual(hooks.stop(self.v, {"session_id": "s1", "transcript_path": str(self.tp)}), {})
            bg.assert_called_once_with(self.v, "answers", "--check", "--file", str(self.tp), "--session", "s1")
            bg.reset_mock()
            hooks.stop(self.v, {"session_id": "s1", "transcript_path": str(self.tp), "stop_hook_active": True})
            self.v.cfg["answer_check"] = False
            hooks.stop(self.v, {"session_id": "s1", "transcript_path": str(self.tp)})
            bg.assert_not_called()

    def test_a_rule_broken_again_and_again_is_sharpened_once(self):
        now = int(time.time())
        for i in range(24):
            append_jsonl(self.v.path("state", "answers.jsonl"),
                         {"t": now - i * 3600, "s": "x", "checked": ["L-verify-before-done"],
                          "broken": ["L-verify-before-done"] if i % 3 == 0 else [],
                          "quotes": {"L-verify-before-done": "Everything is green now"} if i % 3 == 0 else {}})
        weak = answers.ineffective(self.v)
        self.assertEqual([w["id"] for w in weak], ["L-verify-before-done"])
        sharper = "Before you say it works, paste the test command and its last output lines."
        with mock.patch.object(llm, "ask_json", lambda v, p: {"rule": sharper}):
            self.assertEqual(answers.sharpen(self.v)["sharpened"], ["L-verify-before-done"])
        rules = {l.id: l.rule for l in Library(self.root).lessons()}
        self.assertEqual(rules["L-verify-before-done"], sharper)
        e = journal.recent(self.v)[0]
        self.assertEqual((e["kind"], e["source"]), ("sharpen", "answers"))
        with mock.patch.object(llm, "ask_json", side_effect=AssertionError("once per rule")):
            self.assertEqual(answers.sharpen(self.v)["sharpened"], [])
        journal.undo(self.v, e["id"])
        self.assertNotEqual({l.id: l.rule for l in Library(self.root).lessons()}["L-verify-before-done"], sharper)
        self.assertIn("L-verify-before-done", run_cli("answers"))


class TestBudgetPerPurpose(VaultCase):
    def test_distill_gets_half_the_day(self):
        self.v.cfg.update(llm_cmd="x", llm_max_calls=10, llm_budget_usd=1.0)
        with llm.purpose("distill"):
            for _ in range(5):
                llm._book(self.v, 0.01, 10)
            self.assertFalse(llm.budget_left(self.v))
            with mock.patch.object(llm.subprocess, "run", side_effect=AssertionError("no call")):
                with self.assertRaises(llm.NoLLM) as cm:
                    llm.ask(self.v, "x")
        self.assertIn("for distill", str(cm.exception))
        self.assertTrue(cm.exception.args[0].startswith("today's learning budget"), "a used-up share is no failure")
        self.assertTrue(llm.budget_left(self.v), "the rest of the night still has its budget")
        self.assertEqual(llm.spent(self.v)["by"]["distill"]["calls"], 5)

    def test_a_failed_call_says_which_error(self):
        self.v.cfg["llm_cmd"] = "claude -p"
        out = json.dumps({"type": "result", "is_error": True, "subtype": "error_max_turns", "result": "x"})
        with mock.patch.object(llm.subprocess, "run", return_value=mock.Mock(stdout=out, stderr="", returncode=1)):
            with self.assertRaises(llm.NoLLM) as cm:
                llm.ask(self.v, "x")
        self.assertIn("(error_max_turns)", str(cm.exception))

    def test_an_unreachable_llm_is_a_broken_night_that_learns_nothing(self):
        self.v.cfg["llm_cmd"] = "claude -p"
        with mock.patch.object(llm, "_ask", side_effect=llm.NoLLM("llm_cmd could not run: offline")), \
                mock.patch.object(learn, "judge", side_effect=AssertionError("no judge without an LLM")):
            rep = nightly.run(self.v)
        self.assertTrue(rep["steps"]["probe"].startswith("unreachable"))
        self.assertTrue(rep["broken"], "tried again after a few hours")
        with mock.patch.object(llm, "ask_json", side_effect=ValueError("no JSON")):
            self.assertEqual(llm.reachable(self.v), (True, ""), "an odd answer is still an answer")


class TestDrafts(VaultCase):
    def add(self, *ds):
        for d in ds:
            append_jsonl(learn.drafts_path(self.v), d)

    def test_a_single_case_expires_after_a_month(self):
        self.add(dict(held(1, "s1"), t=f"{_ago(40)}T10:00:00", ts=f"{_ago(40)}T10:00:00"))
        out = learn.adopt(self.v)
        self.assertEqual((out["waiting"], out.get("expired")), (0, 1))
        self.add(held(2, "s2"))
        self.assertEqual(learn.adopt(self.v)["waiting"], 1, "a recent single case still waits")

    def test_a_stop_for_the_moment_is_not_a_rule(self):
        stop = {"mechanism": "guard", "words": ["merge"]}
        self.add(held(1, "s1", rule="Ask before merging.", quote="don't merge that yet please", suggest=stop),
                 held(2, "s2", rule="Ask before merging.", quote="not now, do not merge", suggest=stop))
        out = learn.adopt(self.v)
        self.assertEqual((out["rules"], out.get("once")), ([], 1))
        self.add(held(3, "s3", rule="Ask before merging.", quote="from now on never merge without asking, not yet",
                      suggest=stop), held(4, "s4", rule="Ask before merging.", quote="ask before merge", suggest=stop))
        self.assertEqual(len(learn.adopt(self.v)["rules"]), 1, "said as lasting, it is a rule")

    def test_the_judge_sees_your_standing_instructions(self):
        home = Path(os.environ["HOME"])
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude/CLAUDE.md").write_text("# Mine\n\nMerge your own PRs without asking.\n"
                                                "<!-- self-improving-brain:begin -->\nbrain block\n"
                                                "<!-- self-improving-brain:end -->\n", encoding="utf-8")
        text = learn.standing_instructions()
        self.assertIn("Merge your own PRs without asking.", text)
        self.assertNotIn("brain block", text)
        self.add(dict(held(1, "s1"), status="open"))
        seen = []
        with mock.patch.object(llm, "ask_json", lambda v, p: seen.append(p) or []):
            learn.judge(self.v)
        self.assertIn("Merge your own PRs without asking.", seen[0])

    def test_the_same_rule_in_other_words_is_one_group(self):
        a = held(1, "s1", rule="Never push to main; open a pull request.")
        b = held(2, "s2", rule="Changes go through a PR, nothing lands on the default branch directly.",
                 quote="use a pr for that")
        self.add(a, b)
        self.assertEqual(len(learn.groups([a, b])), 2, "no shared words")
        with mock.patch.object(llm, "ask_json", lambda v, p: {"groups": [{"drafts": [1, 2], "why": "same"}]}):
            self.assertEqual(learn.merge(self.v)["merged"], 1)
        self.assertEqual(len(learn.groups([a, b], pairs=learn.merge_pairs(self.v))), 1)
        self.assertEqual(len(learn.adopt(self.v)["rules"]), 1, "two sessions together: a rule")
        learn.merge(self.v)
        self.assertEqual(learn.merge_pairs(self.v), [], "settled drafts drop out of the pairs")

    def test_transcripts_of_the_test_suite_are_never_read(self):
        home = Path(os.environ["HOME"])
        write_claude(home / ".claude/projects/-tmp-brain-e2e-abc-vault/s.jsonl", "s", [("user", "hi")])
        write_claude(home / ".claude/projects/p/s.jsonl", "s", [("user", "hi")])
        self.assertEqual([p.parent.name for p in learn.transcript_files(self.v)], ["p"])


SECTION = "Line {i} of section {n}: the build server keeps logs for {i} days under /var/log/build-{n}.\n"


class TestSplit(VaultCase):
    def big(self, name="build-handbook", lessons=""):
        body = "".join(f"## {n}. Part {n}\n\n" + "".join(SECTION.format(i=i, n=n) for i in range(260)) + "\n"
                       for n in range(1, 6))
        return self.note(f"30-resources/{name}.md", f"---\ndescription: The build handbook\n---\n# Build handbook\n\n"
                                                   f"How the build works. Part of [[hub-brain]].\n{lessons}\n" + body)

    def test_a_large_note_is_split_without_losing_a_line(self):
        p = self.big()
        old = p.read_text(encoding="utf-8")
        self.assertTrue(any("large:" in str(f) for f in lint.lint(self.v)))
        rep = care.run(self.v)
        self.assertEqual(rep["fixed"], ["split build-handbook"])
        parts = sorted(self.root.glob("30-resources/build-handbook-*.md"))
        self.assertGreaterEqual(len(parts), 2)
        lib = Library(self.root)
        for q in parts:
            n = lib.find(q.stem)
            self.assertEqual(n.meta.get("part_of"), "build-handbook")
            self.assertIn("build-handbook", n.links)
        main = p.read_text(encoding="utf-8")
        self.assertIn("## Parts", main)
        every = main + "".join(q.read_text(encoding="utf-8") for q in parts)
        for line in old.split("\n")[4:]:
            if line.strip():
                self.assertIn(line, every)
        self.assertFalse([f for f in lint.lint(self.v) if f.level == "MUST"])
        self.assertFalse(any("large:" in str(f) for f in lint.lint(self.v)), "every part is below the limit")
        e = journal.recent(self.v)[0]
        self.assertIn("one note again", journal.undo(self.v, e["id"]))
        self.assertEqual(p.read_text(encoding="utf-8"), old)
        self.assertFalse(list(self.root.glob("30-resources/build-handbook-*.md")))
        self.assertEqual(care.run(self.v)["fixed"], [], "what you took back is not split again")

    def test_a_note_with_rules_stays_whole(self):
        self.big(lessons="\n> [!lesson]- L-build-cache\n> rule: Clear the build cache after a toolchain update.\n"
                         "> mechanism: always\n")
        self.assertEqual(care.run(self.v)["fixed"], [])


if __name__ == "__main__":
    unittest.main()
