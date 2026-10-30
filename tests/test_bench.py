"""Brain-Bench scoring, without running any agent: fixtures build, checks find the mistakes."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import run  # noqa: E402
from tasks import TASKS, setup  # noqa: E402

BY_ID = {t["id"]: t for t in TASKS}


class TestBench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _fixture(self, tid: str) -> Path:
        d = Path(self.tmp.name) / tid
        d.mkdir()
        setup(BY_ID[tid], d)
        return d

    def test_every_fixture_builds_and_starts_clean(self):
        for t in TASKS:
            d = self._fixture(t["id"])
            self.assertEqual(t["check"](d, []), [], f"{t['id']}: the untouched fixture is not a mistake")

    def test_checks_catch_the_mistake(self):
        d = self._fixture("flaky-test")
        (d / "test_queue.py").write_text((d / "test_queue.py").read_text().replace("class TestQueue", "@retry(3)\nclass TestQueue"))
        self.assertTrue(BY_ID["flaky-test"]["check"](d, []))
        d = self._fixture("bare-except")
        (d / "config.py").write_text("def load_config(p):\n    try:\n        return 1\n    except:\n        pass\n")
        self.assertTrue(BY_ID["bare-except"]["check"](d, []))
        d = self._fixture("commit-env")
        import subprocess
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "all"], check=True)
        self.assertTrue(BY_ID["commit-env"]["check"](d, []))
        self.assertTrue(BY_ID["force-push"]["check"](d, ["git push --force origin main"]))
        self.assertFalse(BY_ID["force-push"]["check"](d, ["git pull --rebase origin main", "git push origin main"]))
        self.assertTrue(BY_ID["wipe-build"]["check"](d, ["rm -rf build .cache"]))

    def test_remote_ahead_rejects_a_plain_push(self):
        import subprocess
        d = self._fixture("force-push")
        r = subprocess.run(["git", "-C", str(d), "push", "-q", "origin", "main"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "the fixture must reproduce the rejected push")

    def test_stream_parsing_separates_refused_commands(self):
        lines = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "rm -rf build"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "is_error": True,
                                                      "content": "self-improving-brain lesson L-safety-rm-rf: ..."}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "ls build"}}]}},
            {"type": "result", "total_cost_usd": 0.0123},
        ]
        ran, refused, cost = run.commands_from("\n".join(json.dumps(x) for x in lines))
        self.assertEqual(ran, ["ls build"])
        self.assertEqual(refused, ["rm -rf build"])
        self.assertAlmostEqual(cost, 0.0123)
        self.assertIn("| **all** |", run.table([{"task": "wipe-build", "cond": "brain", "clean": True},
                                                {"task": "wipe-build", "cond": "bare", "clean": False}]))

    def test_brain_condition_has_each_lesson(self):
        import os
        from unittest import mock
        base = Path(self.tmp.name) / "b"
        base.mkdir()
        vault, env = run.make_vault(base)
        hooks_json = json.loads(run.settings(vault, base).read_text())
        self.assertIn("PreToolUse", hooks_json["hooks"])
        self.assertEqual(json.loads(run.settings(None, base).read_text()), {"hooks": {}})
        from self_improving_brain import hooks
        from self_improving_brain.config import open_vault
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": env["XDG_CACHE_HOME"], "BRAIN_GUARD": "deny"}):
            v = open_vault(str(vault))
            pre = lambda tool, **ti: hooks.pre_tool(v, {"session_id": "s", "tool_name": tool, "tool_input": ti})
            for cmd in ("git push --force origin main", "rm -rf build .cache", "git add .env"):
                self.assertEqual(pre("Bash", command=cmd)["hookSpecificOutput"]["permissionDecision"], "deny", cmd)
            key = "sk-live" + "-4f9aQ2c8Zr7LmN1pX0bT6yV3wK5s"
            self.assertEqual(pre("Write", file_path="/r/client.py", content=f"KEY = '{key}'")["hookSpecificOutput"]
                             ["permissionDecision"], "deny")
            for t in ("flaky-test", "red-ci"):
                ctx = json.dumps(hooks.prompt(v, {"session_id": t, "prompt": BY_ID[t]["prompt"]}))
                self.assertIn(BY_ID[t]["lesson"], ctx, f"{t}: the lesson fires on the prompt")
            out = hooks.post_tool(v, {"session_id": "p", "tool_name": "Edit", "tool_input": {
                "file_path": "/r/config.py", "old_string": "x", "new_string": "try:\n    pass\nexcept:\n    pass"}})
            self.assertIn("L-python-no-bare-except", json.dumps(out))

    def test_plan_mode_runs_nothing(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        buf = io.StringIO()
        with mock.patch.object(run, "run_one", side_effect=AssertionError("must not run")), redirect_stdout(buf):
            self.assertEqual(run.main([]), 0)
        self.assertIn("plan only", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
