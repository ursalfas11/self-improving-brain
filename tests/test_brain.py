"""End-to-end tests on a throwaway vault. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from self_improving_brain import adapters, cli, hooks, inbox, learn, metrics  # noqa: E402
from self_improving_brain.config import open_vault  # noqa: E402
from self_improving_brain.notes import Library, parse_frontmatter  # noqa: E402
from self_improving_brain.search import Index  # noqa: E402
from self_improving_brain.secrets import scan  # noqa: E402
from self_improving_brain.transcripts import parse  # noqa: E402


def run_cli(*args) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(list(args))
    return buf.getvalue()


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "BRAIN_VAULT")}
        os.environ["HOME"] = str(base / "home")
        os.environ["XDG_CONFIG_HOME"] = str(base / "home" / ".config")
        self.root = base / "vault"
        os.environ["BRAIN_VAULT"] = str(self.root)
        run_cli("init", str(self.root))
        self.v = open_vault()
        os.environ.pop("BRAIN_DEBUG", None)

    def tearDown(self):
        for k, val in self.env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        self.tmp.cleanup()

    def note(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestNotes(VaultCase):
    def test_frontmatter_sections_links_lessons(self):
        self.note("20-areas/db.md", "---\nname: db\ndescription: Database setup\ntags: [pg, ops]\n---\n# DB\n\n"
                  "Intro [[hub-brain]].\n\n## Backups\nNightly dumps.\n\n```\n[[not-a-link]]\n```\n\n"
                  "> [!lesson]- L-db-backup\n> rule: Check the last backup before a migration.\n"
                  "> mechanism: reflex\n> words: migration, backup\n")
        lib = Library(self.root)
        n = lib.notes["db"]
        self.assertEqual(n.tags, ["pg", "ops"])
        self.assertEqual([s.title for s in n.sections], ["(intro)", "Backups"])
        self.assertEqual(n.links, ["hub-brain"])
        self.assertEqual(n.lessons[0].id, "L-db-backup")
        self.assertEqual(n.lessons[0].words, ["migration", "backup"])

    def test_template_vault_is_clean(self):
        out = run_cli("lint")
        self.assertIn("0 MUST", out)


class TestSearch(VaultCase):
    def test_ranking_and_learned_terms(self):
        self.note("20-areas/payments.md", "---\nname: payments\ndescription: Stripe webhooks and refunds\n---\n"
                  "# Payments\n\nWebhook signatures are verified with the signing secret name STRIPE_WEBHOOK_SECRET.\n")
        self.note("20-areas/cooking.md", "---\nname: cooking\ndescription: Pasta recipes\n---\n# Cooking\n\nBoil water.\n")
        lib = Library(self.root)
        hits = Index(lib).score("how do we verify stripe webhooks")
        self.assertEqual(hits[0].note.name, "payments")
        self.assertFalse(Index(lib).score("carbonara dinner"))
        learned = {"cooking": {"shown": 0, "hit": 0, "terms": {"carbonara": 3}}}
        self.assertEqual(Index(lib, learned).score("carbonara dinner")[0].note.name, "cooking")


class TestSecrets(unittest.TestCase):
    def test_detects_and_allows(self):
        self.assertTrue(scan("token: ghp_" + "a" * 36))
        self.assertTrue(scan("password = hunter2hunter2"))
        self.assertTrue(scan("postgres://user:s3cretpass@db:5432/x"))
        self.assertFalse(scan("API_KEY=$API_KEY"))
        self.assertFalse(scan("the password lives in 1Password under 'prod db'"))
        self.assertFalse(scan("password = hunter2hunter2  # brain:allow-secret"))


class TestInbox(VaultCase):
    def test_add_dedupe_secret(self):
        inbox.add(self.v, "fact", "The CI cache is keyed on the lockfile hash.")
        with self.assertRaises(inbox.Rejected):
            inbox.add(self.v, "fact", "The CI cache is keyed on the lockfile hash!")
        with self.assertRaises(inbox.Rejected):
            inbox.add(self.v, "fact", "deploy key is sk-ant-" + "x" * 30)
        self.assertEqual(len(inbox.items(self.v)), 1)


def write_claude(path: Path, session: str, msgs: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (role, content) in enumerate(msgs):
        lines.append({"type": role, "sessionId": session, "timestamp": f"2026-10-01T10:00:{i:02d}Z",
                      "message": {"role": role, "content": content}})
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


class TestLearning(VaultCase):
    def test_transcripts_to_lessons_to_hooks(self):
        home = Path(os.environ["HOME"])
        write_claude(home / ".claude/projects/p/a.jsonl", "s1", [
            ("user", "update the readme"),
            ("assistant", [{"type": "text", "text": "Done and pushed."}]),
            ("user", "no, I said do not push without asking"),
            ("assistant", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "git push"}}]),
            ("user", [{"type": "tool_result", "tool_use_id": "t", "is_error": True,
                       "content": "The user doesn't want to proceed with this tool use."}]),
            ("user", "<command-name>/clear</command-name>"),
        ])
        tr = parse(home / ".claude/projects/p/a.jsonl")
        self.assertEqual(len(tr.user_messages), 2)
        st = learn.scan(self.v)
        self.assertEqual(st["corrections"], 2)
        self.assertEqual(st["new_drafts"], 2)
        self.assertEqual(learn.scan(self.v)["new_drafts"], 0, "rescan must not duplicate drafts")

        d = learn.drafts(self.v)[0]
        lesson = learn.approve(self.v, d["id"], rule="Never git push without an explicit OK.", mechanism="guard",
                               commands=r"^git push\b")
        self.assertIn(lesson.id, [l.id for l in Library(self.root).lessons()])

        out = hooks.pre_tool(self.v, {"session_id": "s9", "tool_name": "Bash", "tool_input": {"command": "git push origin"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(hooks.pre_tool(self.v, {"session_id": "s9", "tool_name": "Bash",
                                                 "tool_input": {"command": "git status"}}), {})

    def test_codex_transcript(self):
        p = Path(os.environ["HOME"]) / ".codex/sessions/r.jsonl"
        p.parent.mkdir(parents=True)
        rows = [{"type": "session_meta", "payload": {"id": "cx"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                      "content": [{"type": "input_text", "text": "<environment_context/>"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                      "content": [{"type": "input_text", "text": "that's wrong, use the v2 endpoint"}]}}]
        p.write_text("\n".join(json.dumps(r) for r in rows))
        tr = parse(p)
        self.assertEqual((tr.source, tr.session, len(tr.user_messages)), ("codex", "cx", 1))

    def test_rejected_topic_is_not_drafted_again(self):
        learn.reject(self.v, "none", None)
        from self_improving_brain.util import append_jsonl
        append_jsonl(self.v.path("rejected.jsonl"), {"words": ["emoji", "commit"]})
        write_claude(Path(os.environ["HOME"]) / ".claude/projects/p/b.jsonl", "s2",
                     [("user", "no, no emoji in the commit message")])
        self.assertEqual(learn.scan(self.v)["new_drafts"], 0)


class TestHooks(VaultCase):
    def test_recall_once_per_session_and_word_lessons(self):
        self.note("20-areas/release.md", "---\nname: release\ndescription: How we cut a release and tag versions\n---\n"
                  "# Release\n\nPart of [[hub-brain]].\n\n## Steps\nBump the version, tag, write release notes.\n\n"
                  "> [!lesson]- L-changelog\n> rule: Update CHANGELOG.md in the same commit as the version bump.\n"
                  "> mechanism: reflex\n> words: release, version\n")
        p = {"session_id": "s1", "prompt": "let's cut a release and tag the new version"}
        ctx = hooks.prompt(self.v, p)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("release", ctx)
        self.assertIn("L-changelog", ctx)
        again = hooks.prompt(self.v, p)
        self.assertEqual(again, {}, "same note and lesson must not be injected twice in one session")

    def test_secret_write_is_denied(self):
        out = hooks.pre_tool(self.v, {"session_id": "s", "tool_name": "Write", "tool_input": {
            "file_path": str(self.root / "20-areas/x.md"), "content": "aws AKIAABCDEFGHIJKLMNOP"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_heredoc_body_does_not_trigger(self):
        self.assertEqual(hooks.command_head("cat > f <<EOF\ngit push --force\nEOF"), "cat > f ")

    def test_read_feeds_learning(self):
        hooks.prompt(self.v, {"session_id": "s1", "prompt": "where do lessons go when no topic note fits"})
        hooks.post_tool(self.v, {"session_id": "s1", "tool_name": "Read",
                                 "tool_input": {"file_path": str(self.root / "00-hubs/hub-projects.md")}})
        model = metrics.build_learned(self.v)
        self.assertIn("hub-projects", model)
        self.assertTrue(model["hub-projects"]["terms"])


class TestAdapters(VaultCase):
    def test_install_is_idempotent_and_reversible(self):
        settings = Path(os.environ["HOME"]) / ".claude/settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]}]}}))
        adapters.install(self.v, "claude")
        adapters.install(self.v, "claude")
        data = json.loads(settings.read_text())
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 2)
        adapters.uninstall("claude")
        data = json.loads(settings.read_text())
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "mine")
        self.assertNotIn("self-improving-brain", (Path(os.environ["HOME"]) / ".claude/CLAUDE.md").read_text())

    def test_codex_block(self):
        adapters.install(self.v, "codex")
        text = (Path(os.environ["HOME"]) / ".codex/AGENTS.md").read_text()
        self.assertEqual(text.count(adapters.BEGIN), 1)


class TestSafety(unittest.TestCase):
    def test_init_refuses_foreign_brain_folder(self):
        with tempfile.TemporaryDirectory() as t:
            foreign = Path(t) / "brain"
            (foreign / ".brain").mkdir(parents=True)
            (foreign / "note.md").write_text("private")
            out = run_cli("init", str(foreign))
            self.assertIn("refusing", out)
            self.assertFalse((foreign / ".brain" / "self-improving-brain.json").exists())


class TestFrontmatterRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        from self_improving_brain.notes import dump_frontmatter
        meta = {"name": "x", "description": "a: b", "tags": ["one", "two"]}
        back, body = parse_frontmatter(dump_frontmatter(meta) + "\n# X\n")
        self.assertEqual(back, meta)
        self.assertEqual(body.strip(), "# X")


class TestReviewRegressions(VaultCase):
    def test_lesson_approval_preserves_frontmatter_byte_for_byte(self):
        fm = ('---\nname: ops\ndescription: >-\n  folded text\n  continues\nup: [[hub-brain]]\n'
              'aliases: ["x, y", z]\n# keep me\ntitle: "He said: \\"hi\\""\nupdated: 2020-01-01\n---\n')
        p = self.note("20-areas/ops.md", fm + "# Ops\n\nDeploys and rollbacks. [[hub-brain]]\n")
        meta, _ = parse_frontmatter(p.read_text())
        self.assertEqual(meta["description"], "folded text continues")
        self.assertEqual(meta["aliases"], ["x, y", "z"])
        self.assertEqual(meta["up"], "[[hub-brain]]")
        from self_improving_brain.notes import Lesson
        learn.write_lesson(self.v, p, Lesson(id="L-x", note="ops", rule="Do the thing.", mechanism="always"))
        after = p.read_text()
        self.assertEqual(after.split("---\n")[1], fm.split("---\n")[1].replace("2020-01-01", __import__("self_improving_brain.util").util.today()))
        self.assertIn("> [!lesson]- L-x", after)

    def test_staged_scan_sees_non_ascii_and_spaced_names(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.note("20-areas/Prüfung mit Leerzeichen.md", "password: hunter2hunter2\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        out = run_cli("scan", "--staged")
        self.assertIn("Prüfung mit Leerzeichen.md", out)

    def test_hook_survives_garbage_stdin(self):
        import subprocess
        shim = Path(__file__).resolve().parent.parent / "brain"
        for data in (b"\xff\xfe\x00", b"123", b"[1,2]", b'{"tool_input": "x", "tool_name": "Bash"}'):
            r = subprocess.run([sys.executable, str(shim), "hook", "pre-tool"], input=data, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_command_head_starting_with_heredoc(self):
        self.assertEqual(hooks.command_head("<<EOF\nx\nEOF"), "")

    def test_init_force_keeps_user_files(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "notes"
            d.mkdir()
            (d / "INDEX.md").write_text("# mine\n")
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            (d / ".git/hooks/pre-commit").write_text("#!/bin/sh\necho mine\n")
            run_cli("init", str(d), "--force")
            self.assertEqual((d / "INDEX.md").read_text(), "# mine\n")
            self.assertEqual((d / ".git/hooks/pre-commit").read_text(), "#!/bin/sh\necho mine\n")

    def test_broken_marker_does_not_crash_uninstall(self):
        p = Path(os.environ["HOME"]) / ".codex/AGENTS.md"
        p.parent.mkdir(parents=True)
        p.write_text("mine\n" + adapters.BEGIN + "\nhalf")
        self.assertIn("skip", adapters.uninstall("codex")[0])

    def test_non_object_jsonl_lines_are_ignored(self):
        ev = self.v.path("events", "x.jsonl")
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text('123\n[]\n"s"\n{"k": "recall", "t": "2026-01-01T00:00:00", "notes": []}\n')
        metrics.build_learned(self.v)
        metrics.recall_numbers(self.v)
        tr = Path(os.environ["HOME"]) / ".claude/projects/p/z.jsonl"
        tr.parent.mkdir(parents=True, exist_ok=True)
        tr.write_text('5\n{"type": "user", "sessionId": "s", "timestamp": null, "message": {"role": "user", "content": "no, wrong file"}}\n')
        self.assertEqual(learn.scan(self.v)["corrections"], 1)

    def test_tool_suffix_does_not_overmatch(self):
        from self_improving_brain.notes import Lesson
        l = Lesson(id="L", note="n", rule="r", tools=["Edit"])
        self.assertTrue(hooks.tool_match(l, "Edit"))
        self.assertFalse(hooks.tool_match(l, "MultiEdit"))
        self.assertTrue(hooks.tool_match(Lesson(id="L", note="n", rule="r", tools=["media_upload"]), "mcp__srv__media_upload"))


if __name__ == "__main__":
    unittest.main()
