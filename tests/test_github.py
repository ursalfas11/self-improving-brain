"""Notes that fall behind GitHub: finding references, asking for their state, naming the stale lines,
and the nightly step that files the correction. No test here talks to GitHub: the `gh` call is replaced.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: E402,F401  (first: a throwaway home)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_brain import VaultCase, run_cli  # noqa: E402

from self_improving_brain import distill, freshness, inbox, lint, llm, nightly  # noqa: E402
from self_improving_brain import github_refs as gh  # noqa: E402
from self_improving_brain.notes import Library  # noqa: E402

REPOS = {"api": "acme/api", "web": "acme/web"}


def merged(title="Ports", at="2026-11-02", kind="pr"):
    return {"state": "merged", "at": at, "title": title, "kind": kind, "checked": "2026-11-10T03:00:00"}


def fake_gh(states: dict, calls: list | None = None):
    """A `gh api graphql` stand-in: answers from {owner/repo#n: (typename, state, date, title[, draft])}."""
    import re

    def run(query: str):
        if calls is not None:
            calls.append(query)
        data = {}
        for alias, owner, name, body in re.findall(r'(r\d+_\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\) \{ (.*?) \}(?= r\d+_\d+:| \}$)', query):
            node = {}
            for n in re.findall(r"n(\d+): issueOrPullRequest", body):
                s = states.get(f"{owner}/{name}#{n}")
                if s is None:
                    node[f"n{n}"] = None
                    continue
                typ, state, at, title = s[:4]
                node[f"n{n}"] = {"__typename": typ, "state": state, "title": title, "closedAt": at if state != "OPEN" else None,
                                 **({"mergedAt": at if state == "MERGED" else None, "isDraft": len(s) > 4 and s[4]}
                                    if typ == "PullRequest" else {})}
            data[alias] = node if any(f"{owner}/{name}#" in k for k in states) else None
        return 0, json.dumps({"data": data}), ""
    return run


class TestRefs(unittest.TestCase):
    def refs(self, text, default=""):
        return [(r["repo"], r["n"], r["how"]) for r in gh.find_refs(text, REPOS, default)]

    def test_the_forms_a_note_uses(self):
        self.assertEqual(self.refs("see https://github.com/acme/api/pull/12 and acme/web#3, `api#7`, web #8"),
                         [("acme/api", 12, "url"), ("acme/web", 3, "full"), ("acme/api", 7, "short"), ("acme/web", 8, "short")])

    def test_a_bare_number_continues_a_list_but_is_not_guessed(self):
        self.assertEqual(self.refs("merged: web #571 (signed urls), #567 (grants) and #580"),
                         [("acme/web", 571, "short"), ("acme/web", 567, "list"), ("acme/web", 580, "list")])
        self.assertEqual(self.refs("PR `api#1` on branch x (49 tests), card #10 in review"), [("acme/api", 1, "short")])
        self.assertEqual(self.refs("card #10 and kit #13"), [], "a name that is no known repository is no reference")

    def test_a_note_about_one_repository_gives_bare_numbers_a_home(self):
        self.assertEqual(self.refs("#4 is open", default="acme/api"), [("acme/api", 4, "default")])

    def test_frontmatter_and_code_are_not_prose(self):
        text = "---\nname: x\ndescription: api#1\n---\n# T\n```\napi#2\n```\napi#3\n"
        self.assertEqual(self.refs(text), [("acme/api", 3, "short")])

    def test_repo_map_from_clones(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for folder, url in (("api", "git@github.com:acme/api.git"), ("web-fork", "https://github.com/acme/web"),
                                ("other", "https://gitlab.com/acme/other.git"), ("dup1", "https://github.com/a/same.git"),
                                ("dup2", "https://github.com/b/same.git")):
                (Path(d) / folder / ".git").mkdir(parents=True)
                (Path(d) / folder / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")
            m = gh.repo_map_from_clones([d, d + "/missing"])
        self.assertEqual(m["api"], "acme/api")
        self.assertEqual((m["web"], m["web-fork"]), ("acme/web", "acme/web"))
        self.assertNotIn("other", m)
        self.assertNotIn("same", m, "two repositories with one name: no guess")
        self.assertEqual((m["dup1"], m["dup2"]), ("a/same", "b/same"))


class TestStale(unittest.TestCase):
    def stale(self, text, **states):
        cache = {k.replace("_", "#").replace("api", "acme/api").replace("web", "acme/web"): v for k, v in states.items()}
        return [(f["line_no"], f["ref"]) for f in gh.stale_lines(text, cache, REPOS)]

    def test_open_in_the_note_merged_on_github(self):
        text = ("- open PRs: api#93 (ports), api#94 (draft), web#561 (84 tests).\n"
                "- the cause was the template (api#92). Open: web#562, acceptance.\n"
                "- web#521 (reviewer F, not merged).\n"
                "- Dafür web#522 (offen).\n"
                "- https://github.com/acme/api/pull/9 wartet auf Review\n")
        got = self.stale(text, api_93=merged(), api_94={"state": "open"}, web_561=merged(), api_92=merged(),
                         web_562=merged(), web_521=merged(), web_522=merged(), api_9=merged())
        self.assertEqual(got, [(1, "acme/api#93"), (1, "acme/web#561"), (2, "acme/web#562"), (3, "acme/web#521"),
                               (4, "acme/web#522"), (5, "acme/api#9")])

    def test_what_is_not_a_claim_about_the_reference(self):
        text = ("- PR `api#1` on branch x (49 tests), card #10 in review.\n"      # the word belongs to the card
                "- api#3 (scaffold) and #4 (blocks) merged, acceptance open\n"       # says merged itself
                "- [x] api#7 open\n"                                                  # ticked off
                "- ~~api#8 is open~~\n"                                               # struck through
                "- open PRs: api#11, api#12 (merged)\n"                               # mixed: left alone
                "- api#13 was the fix\n"                                              # says nothing
                "- purpose per the Datenschutz-Entwurf (comment in api #14): tracing\n"  # a compound names a thing
                "- proposed in api#15, nicht gemergt\n")                              # closed without a merge: still right
        got = self.stale(text, api_1=merged(), api_3=merged(), api_4=merged(), api_7=merged(), api_8=merged(),
                         api_11=merged(), api_12=merged(), api_13=merged(), api_14=merged(),
                         api_15={"state": "closed", "at": "2026-11-03", "kind": "pr"})
        self.assertEqual(got, [])

    def test_unmerged_headings_and_drafts(self):
        text = ("- PRs (ungemergt): web#534 (paths), api#74 (ports), api#75 (Entwurf, tools yaml).\n"
                "- Draft-PRs with F: web #714 (routes), api #58 (rights)\n"
                "- proposed in api#15, nicht gemergt\n")
        got = self.stale(text, web_534=merged(), api_74=merged(), api_75=merged(), web_714=merged(), api_58=merged(),
                         api_15=merged())
        self.assertEqual(got, [(1, "acme/web#534"), (1, "acme/api#74"), (1, "acme/api#75"), (2, "acme/web#714"),
                               (2, "acme/api#58"), (3, "acme/api#15")])

    def test_a_guessed_repository_is_weak(self):
        cache = {"acme/api#4": merged()}
        self.assertEqual(gh.stale_lines("#4 is open", cache, REPOS, "acme/api"), [])
        self.assertEqual(len(gh.stale_lines("#4 is open", cache, REPOS, "acme/api", weak=True)), 1)

    def test_claim_and_finding(self):
        fs = gh.stale_lines("- open: api#93, web#561", {"acme/api#93": merged(), "acme/web#561": {
            "state": "closed", "at": "2026-11-03", "title": "", "kind": "issue"}}, REPOS)
        self.assertEqual(gh.claim(fs, "tool-x"), 'GitHub: pull request acme/api#93 (Ports) was merged on 2026-11-02; issue '
                         'acme/web#561 was closed on 2026-11-03. The note tool-x still calls them open: "- open: api#93, web#561"')
        self.assertIn("ist gemergt seit 02.11.2026", gh.claim(fs[0], "tool-x", "de"))
        self.assertEqual(gh.finding(fs[1]), "acme/web#561 was closed on 2026-11-03 (GitHub), but line 1 still calls it open")


class TestFetch(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 12, 3, 0, 0)

    def test_one_call_for_many_and_the_cache_decides_what_is_asked(self):
        calls, cache = [], {"acme/api#1": dict(merged(), checked="2026-11-11T03:00:00"),          # merged: not again
                            "acme/api#2": {"state": "open", "checked": "2026-11-10T03:00:00"}}     # open: again
        states = {"acme/api#2": ("PullRequest", "MERGED", "2026-11-11T09:00:00Z", "Two"),
                  "acme/api#3": ("PullRequest", "OPEN", "", "Three", True),
                  "acme/web#5": ("Issue", "CLOSED", "2026-11-01T00:00:00Z", "Five")}
        rep = gh.fetch_states(["acme/api#1", "acme/api#2", "acme/api#3", "acme/web#5", "acme/web#6", "nonsense"],
                              cache, now=self.NOW, run=fake_gh(states, calls))
        self.assertEqual((rep["asked"], rep["calls"], rep["errors"]), (4, 1, []))
        self.assertNotIn("n1:", calls[0])
        self.assertEqual((cache["acme/api#2"]["state"], cache["acme/api#2"]["at"], cache["acme/api#2"]["kind"]),
                         ("merged", "2026-11-11", "pr"))
        self.assertEqual(cache["acme/api#3"]["state"], "draft")
        self.assertEqual((cache["acme/web#5"]["state"], cache["acme/web#5"]["kind"]), ("closed", "issue"))
        self.assertEqual(cache["acme/web#6"]["state"], "unknown", "no such item: asked again in a week, not every night")
        self.assertFalse(gh.due(cache["acme/web#6"], self.NOW + dt.timedelta(days=6)))
        self.assertTrue(gh.due(cache["acme/web#6"], self.NOW + dt.timedelta(days=8)))

    def test_batches_and_the_call_cap(self):
        calls, cache = [], {}
        keys = [f"acme/api#{i}" for i in range(1, 151)]
        rep = gh.fetch_states(keys, cache, now=self.NOW, run=fake_gh({}, calls), batch=60, max_calls=2)
        self.assertEqual((rep["calls"], len(calls), len(cache)), (2, 2, 120))
        self.assertIn("the rest waits", rep["errors"][0])

    def test_no_login_changes_nothing(self):
        cache = {"acme/api#1": {"state": "open", "checked": "2026-11-01T00:00:00"}}
        before = json.dumps(cache)
        rep = gh.fetch_states(["acme/api#1", "acme/api#2"], cache, now=self.NOW,
                              run=lambda q: (4, "", "gh: To get started with GitHub CLI, please run: gh auth login"))
        self.assertEqual((json.dumps(cache), rep["calls"]), (before, 1))
        self.assertIn("gh auth login", rep["errors"][0])

    def test_a_repo_that_vanished_does_not_unmerge(self):
        cache = {"acme/api#1": dict(merged(), checked="2026-01-01T00:00:00")}
        gh.fetch_states(["acme/api#1"], cache, now=self.NOW, run=fake_gh({}))
        self.assertEqual((cache["acme/api#1"]["state"], cache["acme/api#1"]["checked"]), ("merged", "2026-11-12T03:00:00"))


NOTE = """---
name: checkout
description: The checkout rework, its pull requests and what is left
type: project
updated: 2026-11-01
---
# Checkout

## State
- open PRs: api#93 (ports), web#561 (tests).
- api#94 is a draft.

Part of [[hub-projects]].
"""


class TestNightlyStep(VaultCase):
    STATES = {"acme/api#93": ("PullRequest", "MERGED", "2026-11-02T10:00:00Z", "Ports"),
              "acme/web#561": ("PullRequest", "OPEN", "", "Tests"),
              "acme/api#94": ("PullRequest", "OPEN", "", "Later", True)}

    def setUp(self):
        super().setUp()
        self.v.set("github_repos", REPOS)
        self.note("00-hubs/hub-projects.md", "---\nname: hub-projects\ndescription: all projects\ntype: hub\n---\n# Projects\n- [[checkout]]\n")
        self.path = self.note("10-projects/checkout.md", NOTE)

    def test_a_stale_line_is_filed_once(self):
        calls = []
        rep = freshness.run(self.v, runner=fake_gh(self.STATES, calls))
        self.assertEqual(rep, {"refs": 3, "asked": 3, "stale": 1, "filed": 1})
        (item,) = inbox.items(self.v)
        self.assertEqual((item["type"], item["source"], item["note"]), ("status", "github", "checkout"))
        self.assertIn("acme/api#93 (Ports) was merged on 2026-11-02", item["claim"])
        self.assertNotIn("web#561 (Tests)", item["claim"].split("The note")[0], "still open on GitHub: not part of the claim")
        again = freshness.run(self.v, runner=fake_gh(self.STATES, calls))
        self.assertEqual((again["asked"], again["filed"], len(inbox.items(self.v))), (0, 0, 1), "cached, and said once")
        inbox.close(self.v, item["id"], "dropped")
        self.assertEqual(freshness.run(self.v, runner=fake_gh(self.STATES))["filed"], 0, "you dropped it: not filed again")

    def test_a_finding_that_reads_like_a_waiting_claim_is_filed_later_not_lost(self):
        states = {"acme/api#1": ("PullRequest", "MERGED", "2026-11-02T10:00:00Z", "Ports"),
                  "acme/api#2": ("PullRequest", "MERGED", "2026-11-02T10:00:00Z", "Ports")}
        self.note("10-projects/twins.md", "---\nname: twins\ndescription: two lines that read alike\n---\n# Twins\n"
                  "- api#1 is still open.\n\n- api#2 is still open.\n")
        first = freshness.run(self.v, runner=fake_gh(states))
        self.assertEqual((first["stale"], first["filed"]), (2, 1), "the second claim is too close to the first")
        (item,) = inbox.items(self.v)
        self.assertEqual(freshness.run(self.v, runner=fake_gh(states))["filed"], 0, "still waiting")
        inbox.close(self.v, item["id"], "done")
        later = freshness.run(self.v, runner=fake_gh(states))
        self.assertEqual((later["filed"], len(inbox.items(self.v))), (1, 1), "filed once the first one is done")

    def test_lint_shows_it_from_the_cache_without_a_call(self):
        freshness.run(self.v, runner=fake_gh(self.STATES))
        with mock.patch.object(gh, "_run_gh", side_effect=AssertionError("lint must not call GitHub")):
            msgs = [str(f) for f in lint.lint(self.v)]
        self.assertTrue(any("behind GitHub: acme/api#93 was merged on 2026-11-02" in m for m in msgs), msgs)

    def test_distill_corrects_the_line_the_claim_names(self):
        freshness.run(self.v, runner=fake_gh(self.STATES))
        self.v.set("llm_cmd", "fake")
        seen = {}

        def answer(v, prompt, **kw):
            seen["prompt"] = prompt
            return {"action": "update", "note": "checkout", "old": "- open PRs: api#93 (ports), web#561 (tests).",
                    "text": "- api#93 (ports) merged on 2026-11-02; open: web#561 (tests).", "reason": "GitHub says merged"}
        with mock.patch.object(llm, "ask_json", side_effect=answer), mock.patch.object(llm, "available", return_value=True):
            out = distill.auto(self.v)
        self.assertIn("update → 10-projects/checkout.md", out[0])
        self.assertIn("the claim is about this note", seen["prompt"])
        self.assertIn("- api#94 is a draft.", seen["prompt"], "the lines around the stale one are part of the prompt")
        self.assertIn("api#93 (ports) merged on 2026-11-02", self.path.read_text(encoding="utf-8"))
        self.assertEqual(freshness.findings(self.v, Library(self.root)), [], "the note says what GitHub says")

    def test_off_no_gh_and_a_note_with_repo_field(self):
        self.v.set("github_check", False)
        self.assertEqual(freshness.run(self.v, runner=fake_gh(self.STATES)), "off")
        self.v.set("github_check", True)
        with mock.patch.object(gh, "gh_available", return_value=False):
            self.assertEqual(freshness.run(self.v), "no gh CLI")
        self.note("10-projects/api-notes.md", "---\nname: api-notes\ndescription: notes about the api repository and its state\n"
                  "repo: api\n---\n# Api\n- #93 is open\n\nPart of [[hub-projects]].\n")
        rep = freshness.run(self.v, runner=fake_gh(self.STATES))
        self.assertEqual((rep["stale"], rep["filed"]), (2, 1), "the guessed one is shown, not filed")

    def test_the_nightly_run_has_the_step_and_the_cli_reports(self):
        with mock.patch.object(gh, "_run_gh", side_effect=fake_gh(self.STATES)), \
                mock.patch.object(gh, "gh_available", return_value=True):
            rep = nightly.run(self.v)
            self.assertEqual(rep["steps"]["github"], {"refs": 3, "asked": 3, "stale": 1, "filed": 1})
            out = run_cli("github")
        self.assertIn("10-projects/checkout.md:10  acme/api#93 was merged on 2026-11-02 (GitHub)", out)
        self.assertIn("3 references in your notes · 0 asked · 1 behind GitHub", out)


if __name__ == "__main__":
    unittest.main()
