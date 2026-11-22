"""Autoguard (a rule that keeps coming back becomes a guard, proven by a replay), the guard's `requires:` proof, and
the rule that a change which only takes triggers away needs no evidence.

No test here calls a real LLM: the judge and the drafter are stubs that read the prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_brain import run_cli  # noqa: E402
from test_evidence import HEAD, EvidenceCase, callout  # noqa: E402

from self_improving_brain import autoguard, hooks, journal, learn, llm, nightly, trust  # noqa: E402
from self_improving_brain.notes import Library, parse_lessons  # noqa: E402

WAIT = callout("L-wait", "Never wait with sleep; poll the run status instead.", words="sleep, wait", since="2026-09-01",
               commands=r"\bsleep\s+\d+")
FIX = "Instead of sleep: poll `gh run view` until it ends."


def lesson(v, lid="L-wait"):
    return next(x for x in Library(v.root).lessons(retired=True) if x.id == lid)


class GuardCase(EvidenceCase):
    """A vault with one reflex that keeps being broken, and real-looking sessions to replay."""

    def setUp(self):
        super().setUp()
        self.note("20-areas/ops.md", HEAD.format("ops") + WAIT)
        self.rounds = 0

    def write_session(self, name, steps, day="2026-10-01"):
        """steps: ('user', text) | ('bash', cmd) | ('rejected', cmd). One transcript as Claude Code writes it."""
        p = Path(os.environ["HOME"]) / ".claude/projects/p" / f"{name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for i, (kind, text) in enumerate(steps):
            ts = f"{day}T10:{i // 60:02d}:{i % 60:02d}Z"
            base = {"sessionId": name, "timestamp": ts}
            if kind == "user":
                rows.append(dict(base, type="user", origin={"kind": "human"}, message={"role": "user", "content": text}))
                continue
            rows.append(dict(base, type="assistant", message={"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": text}}]}))
            result = "The user doesn't want to proceed with this tool use." if kind == "rejected" else "ok"
            rows.append(dict(base, type="user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": result, "is_error": kind == "rejected"}]}))
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    def relapses(self, n=3, extra=True):
        for k in range(n):
            steps = [("user", "deploy it and wait for the pipeline"), ("bash", "git status"),
                     ("bash", "sleep 120 && gh run view"),
                     ("user", "I told you never to wait with sleep, poll the run status instead")]
            if extra:
                steps += [("user", "now run the tests"), ("bash", "pytest -q"), ("bash", "sleep 2"), ("bash", "git diff")]
            self.write_session(f"r{k}", steps)

    # the stubs: they read what the real prompt says
    def judge(self, prompt):
        if "Each case lists" in prompt or "EXACTLY this rule" in prompt:
            cases = []
            for n, body in re.findall(r"CASE (\d+) \(.*?\n(.*?)(?=\nCASE |\Z)", prompt, re.S):
                k = next((int(m.group(1)) for m in re.finditer(r"\[(\d+)\] Bash: .*sleep", body)), -1)
                cases.append({"n": int(n), "about": True, "call": k, "why": "waits with sleep"})
            return {"cases": cases}
        if "HIT " in prompt:
            hits = []
            for n, body in re.findall(r"HIT (\d+) \(.*?\n(.*?)(?=\nHIT |\Z)", prompt, re.S):
                secs = int((re.search(r"sleep (\d+)", body) or [0, 0])[1])
                hits.append({"n": int(n), "justified": secs >= 30, "why": "a short wait on something local"})
            return {"hits": hits}
        raise AssertionError("unexpected prompt: " + prompt[:80])

    def drafter(self, *drafts):
        def draw(prompt):
            if "You draft a guard" not in prompt:
                return self.judge(prompt)
            d = drafts[min(self.rounds, len(drafts) - 1)]
            self.rounds += 1
            return d
        return draw

    NARROW = {"detectable": True, "why": "", "rule": FIX, "commands": r"\bsleep\s+\d{2,}\b"}
    BROAD = {"detectable": True, "why": "", "rule": FIX, "commands": r"\bsleep\b"}

    def run_night(self, *drafts, think=True, **kw):
        d = self.drafter(*drafts) if drafts else None
        return autoguard.run(self.v, think=think, judge=d or self.judge, drafter=d, **kw)


class TestPromote(GuardCase):
    def test_a_rule_that_keeps_coming_back_becomes_a_guard_after_the_replay(self):
        self.relapses()
        self.assertEqual(self.bash("sleep 120"), "", "a reflex does not ask")
        res = self.run_night(self.BROAD, self.NARROW)
        self.assertEqual((res["guarded"], res["refused"], self.rounds), (["L-wait"], {}, 2),
                         "the broad draft asked about `sleep 2`; the judge said so and the second draft is narrow")
        l = lesson(self.v)
        self.assertEqual((l.mechanism, l.commands, l.rule), ("guard", self.NARROW["commands"], FIX))
        self.assertEqual((l.since, l.words), ("2026-09-01", ["sleep", "wait"]), "what the rule knew stays")
        self.assertEqual(self.bash("sleep 120 && echo done"), "ask")
        self.assertEqual(self.bash("sleep 2"), "", "the short wait is none of its business")
        e = self.kinds("guard")[0]
        self.assertEqual((e["lesson"], e["before"], e["after"]), ("L-wait", "reflex", "guard"))
        self.assertIn("3 real breaches", e["why"])
        self.assertIn("mechanism: reflex", e["block_before"])
        self.assertIn("guard", run_cli("review", "--no-input"))
        self.assertEqual([x.id for x in trust.effective(self.v, [l], record_seen=False)], ["L-wait"], "it still counts")

    def test_undo_puts_the_old_callout_back_and_the_night_never_promotes_it_again(self):
        self.relapses()
        self.run_night(self.NARROW)
        e = self.kinds("guard")[0]
        self.assertIn("is reflex again", journal.undo(self.v, e["id"]))
        l = lesson(self.v)
        self.assertEqual((l.mechanism, l.commands), ("reflex", r"\bsleep\s+\d+"))
        self.assertEqual(self.bash("sleep 120"), "")
        res = self.run_night(self.NARROW)
        self.assertEqual(res["candidates"], 0, "your veto stands")
        self.assertIn("you took the guard back", autoguard.pause(self.v, "L-wait"))

    def test_undo_refuses_when_you_changed_the_callout_since(self):
        self.relapses()
        self.run_night(self.NARROW)
        p = self.root / "20-areas/ops.md"
        p.write_text(p.read_text().replace("sleep\\s+\\d{2,}", "sleep\\s+\\d{3,}"))
        with self.assertRaises(ValueError):
            journal.undo(self.v, self.kinds("guard")[0]["id"])
        self.assertEqual(lesson(self.v).mechanism, "guard")

    def test_a_draft_that_keeps_asking_in_vain_is_refused_and_not_retried_at_once(self):
        self.relapses()
        res = self.run_night(self.BROAD)
        self.assertEqual(res["guarded"], [])
        self.assertIn("false hits", res["refused"]["L-wait"])
        self.assertEqual(lesson(self.v).mechanism, "reflex", "nothing written")
        self.assertEqual(self.run_night(self.BROAD)["candidates"], 0, "a refused draft waits RETRY_DAYS")

    def test_a_draft_with_too_many_hits_is_too_broad_without_asking_the_judge(self):
        self.relapses()
        with mock.patch.object(autoguard, "MAX_HITS", 3):
            res = self.run_night(self.BROAD)
        self.assertIn("too broad", res["refused"]["L-wait"])

    def test_a_mistake_that_shows_in_no_single_call_stays_as_text(self):
        self.relapses()
        res = self.run_night({"detectable": False, "why": "the mistake is in the plan"})
        self.assertEqual((res["guarded"], list(res["stays"])), ([], ["L-wait"]))
        self.assertIn("not recognisable", res["stays"]["L-wait"])

    def test_without_a_proven_call_there_is_no_draft(self):
        self.relapses()
        judge = lambda p: {"cases": [{"n": i, "about": True, "call": -1, "why": "in the answer"} for i in range(1, 4)]}  # noqa: E731
        res = autoguard.run(self.v, think=True, judge=judge, drafter=lambda p: self.fail("asked for a draft"))
        self.assertIn("no proven breach", res["stays"]["L-wait"])

    def test_a_draft_that_fires_on_everyday_commands_never_reaches_the_replay(self):
        self.relapses()
        res = self.run_night({"detectable": True, "rule": FIX, "commands": r"git"})
        self.assertIn("everyday", res["refused"]["L-wait"])

    def test_it_waits_without_a_model_and_dry_run_writes_nothing(self):
        self.relapses()
        self.assertIn("no LLM CLI", autoguard.run(self.v, think=False)["waiting"])
        res = self.run_night(self.NARROW, dry=True)
        self.assertEqual((res["guarded"], res["drafts"]["L-wait"]["commands"]), (["L-wait"], self.NARROW["commands"]))
        self.assertEqual((lesson(self.v).mechanism, self.kinds("guard")), ("reflex", []))

    def test_a_model_that_is_gone_is_tomorrows_problem(self):
        self.relapses()
        with mock.patch.object(llm, "ask_json", side_effect=llm.NoLLM("today's learning budget is used up")):
            self.v.cfg["llm_cmd"] = "x"
            res = autoguard.run(self.v, think=True)
        self.assertIn("budget", res["waiting"])
        self.assertEqual(self.run_night(self.NARROW)["guarded"], ["L-wait"], "no failed try was booked")

    def test_two_relapses_are_not_enough_and_off_means_off(self):
        self.relapses(n=2)
        self.assertEqual(self.run_night(self.NARROW)["candidates"], 0)
        self.relapses(n=3)
        self.v.cfg["autoguard"] = False
        self.assertEqual(autoguard.run(self.v, think=True), {"off": True})
        self.assertNotIn("autoguard", nightly.run(self.v)["steps"])

    def test_a_rule_an_agent_wrote_is_not_built_on_until_it_counts(self):
        self.relapses()
        tp = self.session("w", ["carry on"])
        self.agent_writes("more", callout("L-agent", "Never wait with sleep for a deploy.", words="sleep, wait",
                                          commands=r"\bsleep\s+\d+"), "w", tp)
        self.assertEqual([l.id for l in autoguard.candidates(self.v, autoguard.load_corpus(self.v))], ["L-wait"])


class TestStepBack(GuardCase):
    def guarded(self):
        self.relapses()
        self.run_night(self.NARROW)
        self.assertEqual(lesson(self.v).mechanism, "guard")

    def today_session(self, name, kinds):
        self.write_session(name, [(k, "sleep 120 && gh run view") for k in kinds], day=date.today().isoformat())

    def test_a_guard_you_let_through_every_time_goes_back_and_waits_before_the_next_try(self):
        self.guarded()
        self.today_session("w1", ["bash"] * 6)
        res = self.run_night(self.NARROW)
        self.assertIn("6 of 6", res["lifted"]["L-wait"])
        l = lesson(self.v)
        self.assertEqual((l.mechanism, l.commands), ("reflex", r"\bsleep\s+\d+"))
        kinds = [e["kind"] for e in journal.entries(self.v) if e["kind"] in ("guard", "unguard")]
        self.assertEqual(kinds, ["guard", "unguard"])
        self.assertEqual(self.kinds("guard")[0]["status"], "undone")
        self.assertIn("stepped back", autoguard.pause(self.v, "L-wait"))
        self.assertEqual(res.get("candidates", 0), 0, "no new promotion during the pause")
        # your veto on the step back: the guard returns
        un = self.kinds("unguard")[0]
        self.assertIn("is a guard again", journal.undo(self.v, un["id"]))
        self.assertEqual(lesson(self.v).mechanism, "guard")

    def test_a_guard_you_reject_stays(self):
        self.guarded()
        self.today_session("w2", ["rejected", "rejected", "rejected", "bash", "rejected", "rejected"])
        res = self.run_night(self.NARROW)
        self.assertNotIn("lifted", res)
        self.assertEqual(lesson(self.v).mechanism, "guard")

    def test_too_few_asks_decide_nothing(self):
        self.guarded()
        self.today_session("w3", ["bash"] * 3)
        self.assertNotIn("lifted", self.run_night(self.NARROW))


class TestRequires(EvidenceCase):
    GUARD = callout("L-ci", "Never push before the CI status was read; run `gh run view` first.", "guard",
                    commands=r"\bgit\s+push\b", requires=r"gh\s+run\s+view")

    def log(self, name, *cmds):
        p = Path(os.environ["HOME"]) / ".claude/projects/p" / f"{name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "assistant", "sessionId": name, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}} for i, c in enumerate(cmds)]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    def test_the_guard_asks_only_while_the_proof_is_missing(self):
        self.note("20-areas/ops.md", HEAD.format("ops") + self.GUARD)
        l = parse_lessons(self.GUARD, "ops")[0]
        self.assertEqual((l.requires, l.mechanism), (r"gh\s+run\s+view", "guard"))
        self.assertIn("> requires: gh\\s+run\\s+view", l.as_callout())
        self.assertEqual(self.bash("git push origin x"), "ask", "no log: the guard asks, the proof can be fetched")
        self.assertEqual(self.bash("git push origin x", self.log("a", "git status")), "ask")
        self.assertEqual(self.bash("git push origin x", self.log("b", "git status", "cd x && gh run view 12")), "")
        self.assertEqual(self.bash("git status", self.log("c")), "")

    def test_requires_is_part_of_what_the_trust_ledger_vouches_for(self):
        a = parse_lessons(self.GUARD, "ops")[0]
        b = parse_lessons(self.GUARD.replace("gh\\s+run\\s+view", "echo ok"), "ops")[0]
        self.assertNotEqual(trust.fingerprint(a), trust.fingerprint(b))
        self.assertIn("requires matches everything", trust.locked(parse_lessons(self.GUARD.replace(r"gh\s+run\s+view", ".*"), "ops")[0]))


class TestNarrowing(EvidenceCase):
    OPS = callout("L-nar", "Check the state before nar commands.", tools="nar_tool, nap_tool", words="nar, state")

    def before(self):
        return parse_lessons(self.OPS, "ops")[0].as_callout()

    def after(self, **fields):
        l = parse_lessons(self.OPS, "ops")[0]
        for k, x in fields.items():
            setattr(l, k, x)
        return l

    def test_only_taking_triggers_away_is_a_narrowing(self):
        old = self.before()
        self.assertIn("only triggers narrowed (nap_tool)", trust.narrowed(self.after(tools=["nar_tool"]), old))
        self.assertTrue(trust.narrowed(self.after(words=["nar"]), old))
        self.assertEqual(trust.narrowed(self.after(), old), "", "nothing changed")
        for changed in ({"tools": ["nar_tool", "nip_tool"]}, {"words": ["nar", "state", "more"]},
                        {"rule": "Delete the state before nar commands."}, {"mechanism": "always"},
                        {"commands": r"nar\b"}, {"check": "x"}, {"tools": [], "words": []}):
            self.assertEqual(trust.narrowed(self.after(**changed), old), "", changed)

    def test_never_for_a_hard_mechanism(self):
        g = callout("L-gd", "Never drop a table.", "guard", tools="drop_tool, wipe_tool")
        old = parse_lessons(g, "ops")[0]
        new = parse_lessons(g, "ops")[0]
        new.tools = ["drop_tool"]
        self.assertEqual(trust.narrowed(new, old.as_callout()), "", "narrowing a guard can disarm it")
        a = parse_lessons(callout("L-al", "Answer briefly.", "always", words="short, brief"), "ops")[0]
        b = parse_lessons(callout("L-al", "Answer briefly.", "always", words="short, brief"), "ops")[0]
        b.words = ["short"]
        self.assertEqual(trust.narrowed(b, a.as_callout()), "")

    def test_an_agent_that_narrows_a_rule_needs_no_evidence_and_is_not_set_back(self):
        p = self.note("20-areas/ops.md", HEAD.format("ops") + self.OPS)
        self.assertTrue(self.reaches_agent("L-nar", "run nar and check the state"), "yours by hand: trusted")
        text = p.read_text().replace("tools: nar_tool, nap_tool", "tools: nar_tool")
        p.write_text(text)
        tp = self.session("n1", ["carry on"], hours_old=3)
        out = json.dumps(hooks.post_tool(self.v, {"session_id": "n1", "tool_name": "Edit", "transcript_path": str(tp),
                                                  "tool_input": {"file_path": str(p), "new_string": "x"}}))
        self.assertIn("only triggers were taken away", out)
        self.assertNotIn("stay silent", out)
        self.assertEqual(trust.settle(self.v, judge=lambda l, w: None), {"backed": [], "discarded": [], "waiting": []})
        self.assertNotIn("nap_tool", p.read_text())
        rel = self.kinds("release")[0]
        self.assertEqual((rel["how"], rel["lesson"]), ("narrowing", "L-nar"))
        self.assertIn("narrowed", run_cli("review", "--no-input"))
        # an addition in the same change is not a narrowing: it goes back without your words
        p.write_text(p.read_text().replace("tools: nar_tool", "tools: nar_tool, nup_tool"))
        tp = self.session("n2", ["carry on"], hours_old=3)
        hooks.post_tool(self.v, {"session_id": "n2", "tool_name": "Edit", "transcript_path": str(tp),
                                 "tool_input": {"file_path": str(p), "new_string": "x"}})
        self.assertEqual(trust.settle(self.v, judge=lambda l, w: {"backed": False, "quote": ""})["discarded"], ["L-nar"])
        self.assertNotIn("nup_tool", p.read_text())

    def test_undoing_a_narrowing_does_not_block_the_topic(self):
        p = self.note("20-areas/ops.md", HEAD.format("ops") + self.OPS)
        self.reaches_agent("L-nar", "nar state")
        p.write_text(p.read_text().replace("tools: nar_tool, nap_tool", "tools: nar_tool"))
        tp = self.session("n3", ["carry on"], hours_old=3)
        hooks.post_tool(self.v, {"session_id": "n3", "tool_name": "Edit", "transcript_path": str(tp),
                                 "tool_input": {"file_path": str(p), "new_string": "x"}})
        e = self.kinds("release")[0]
        journal.undo(self.v, e["id"])
        self.assertEqual(learn.rejected_topics(self.v), [], "no topic was blocked")


if __name__ == "__main__":
    import unittest
    unittest.main()
