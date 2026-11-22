"""The gold builder: a session never lands in both halves, candidates come from the notes an agent opened after a
question, the judge's verdicts become gold. Invented sessions, a stubbed LLM, no network."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from self_improving_brain.notes import Library  # noqa: E402
from self_improving_brain.search import Index  # noqa: E402

_spec = importlib.util.spec_from_file_location("gold_build", Path(__file__).resolve().parent.parent / "eval" / "gold_build.py")
gb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gb)


def session(path: Path, sid: str, events: list) -> Path:
    """A Claude Code style transcript: events are ("user", text) or ("read", note name)."""
    lines = []
    for kind, val in events:
        if kind == "user":
            lines.append({"type": "user", "sessionId": sid, "message": {"role": "user", "content": val}})
        else:
            lines.append({"type": "assistant", "sessionId": sid, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": f'brain read {val}'}}]}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


class TestGoldBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        notes = self.root / "notes"
        notes.mkdir()
        for name, text in (("deploy-rollback", "Roll back with the previous release tag."),
                           ("api-keys", "Rotate the payment keys every quarter."),
                           ("vpn-access", "Ask your lead to approve vpn access.")):
            (notes / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n# {name}\n\n{text}\n")
        self.lib = Library(notes)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_split_is_per_session_and_stable(self):
        a = gb.split_of_session("session-1")
        self.assertEqual(a, gb.split_of_session("session-1"))
        halves = {gb.split_of_session(f"s{i}") for i in range(40)}
        self.assertEqual(halves, {"dev", "test"})

    def test_candidates_pair_a_question_with_the_notes_read_after_it(self):
        f = session(self.root / "s.jsonl", "sess-a", [
            ("user", "how do we roll back a failed deployment on staging"), ("read", "deploy-rollback"),
            ("user", "ok"), ("read", "api-keys"),                            # too short: not a question
            ("user", "when do the payment keys have to be rotated again?"), ("read", "api-keys"), ("read", "api-keys"),
            ("user", "thanks, and something nobody looked up in the vault")])
        rows = gb.candidates(self.lib, [f])
        self.assertEqual([r["read"] for r in rows], [["deploy-rollback"], ["api-keys"]])
        self.assertTrue(all(r["split"] == gb.split_of_session("sess-a") for r in rows))

    def test_the_judge_keeps_central_notes_and_drops_the_rest(self):
        row = {"id": "x1", "question": "how do we roll back a failed deployment", "read": ["deploy-rollback"],
               "session": "sess-a", "split": "dev"}
        answer = {"kind": "knowledge", "notes": [{"name": "deploy-rollback", "verdict": "central"},
                                                 {"name": "vpn-access", "verdict": "no"}]}
        with mock.patch.object(gb.llm, "ask_json", return_value=answer):
            g = gb.judge_one(object(), self.lib, Index(self.lib, {}), row)
        self.assertEqual(g["gold"], ["deploy-rollback"])
        self.assertEqual(g["split"], "dev")
        with mock.patch.object(gb.llm, "ask_json", return_value={"kind": "context", "notes": []}):
            self.assertEqual(gb.judge_one(object(), self.lib, Index(self.lib, {}), row)["gold"], [])
        with mock.patch.object(gb.llm, "ask_json", side_effect=ValueError("no json")):
            self.assertIsNone(gb.judge_one(object(), self.lib, Index(self.lib, {}), row))

    def test_the_judge_prompt_is_marked_internal(self):
        self.assertTrue(gb.PROMPT.startswith(gb.llm.MARKER))  # `brain learn` skips transcripts of our own calls

    def test_check_finds_a_session_in_both_halves_and_a_missing_note(self):
        p = self.root / "gold.jsonl"
        rows = [{"id": "1", "question": "q", "gold": ["nope"], "kind": "knowledge", "split": "dev", "session": "s"},
                {"id": "2", "question": "q2", "gold": ["api-keys"], "kind": "knowledge", "split": "test", "session": "s"}]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        bad = "\n".join(gb.check(p, self.lib))
        self.assertIn("both halves", bad)
        self.assertIn("not in the vault", bad)


if __name__ == "__main__":
    unittest.main()
