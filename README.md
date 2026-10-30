<div align="center">

<img src="docs/assets/banner.svg" alt="self-improving-brain: your coding agent forgets everything between sessions. This brain gets smarter with every one." width="100%">

<br>

<h3>The self-improving memory &amp; second brain for AI coding agents</h3>

<p>
It turns your corrections into rules, injects the right rule at the right moment,<br>
stops the agent before irreversible mistakes, and <b>proves that your agent makes fewer of them</b>.
</p>

<p>
<a href="https://github.com/ursalfas11/self-improving-brain/actions/workflows/ci.yml"><img src="https://github.com/ursalfas11/self-improving-brain/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<img src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+">
<img src="https://img.shields.io/badge/dependencies-0-7cf7d4" alt="Zero dependencies">
<img src="https://img.shields.io/badge/server-none-8b9bff" alt="No server">
<img src="https://img.shields.io/badge/license-MIT-f08cff" alt="MIT license">
<a href="https://github.com/ursalfas11/self-improving-brain/stargazers"><img src="https://img.shields.io/github/stars/ursalfas11/self-improving-brain?style=social" alt="GitHub stars"></a>
</p>

<p>
<a href="#-quickstart"><b>Quickstart</b></a> ·
<a href="#-how-it-gets-smarter"><b>How it gets smarter</b></a> ·
<a href="#-prove-it"><b>Prove it</b></a> ·
<a href="#-lessons"><b>Lessons</b></a> ·
<a href="#-works-with-every-agent"><b>Agents</b></a> ·
<a href="docs/architecture.md"><b>Architecture</b></a>
</p>

</div>

<br>

<p align="center">
  <img src="docs/assets/demo.svg" alt="Demo: on Tuesday the user corrects the agent; that night brain learn turns it into a lesson; on Friday the lesson fires before the agent repeats the mistake" width="92%">
</p>

## 🤯 The problem

You told your agent *"no, don't paper over flaky tests with retries"* last Tuesday. Today it adds a retry again.

Agent mistakes rarely come from missing facts. They come from a rule the agent *knew* but didn't apply **at the moment it mattered**. This brain works on exactly that moment, and then it checks whether the mistake comes back.

<table>
<tr>
<td width="33%" valign="top">

### 🧠 Learns from you
`brain learn` turns every *"no, why did you…"* and every rejected tool call in your transcripts into a lesson draft. Repeated procedures become **skills**. You OK each one in one line.

</td>
<td width="33%" valign="top">

### ⚡ Fires at the right moment
Prompt words, a tool, a shell command or a path trigger it. A `guard` makes the agent **ask you** before `git push --force`, `rm -rf` or `DROP TABLE`.

</td>
<td width="33%" valign="top">

### 📉 Proves it works
`brain replay` shows which of your past corrections today's lessons would have caught. `brain stats` tracks mistakes per 100 messages, week by week.

</td>
</tr>
<tr>
<td valign="top">

### 🛡️ Rules that check themselves
A lesson can carry a `check:` regex. When the agent writes `@retry` anyway, the brain notices without you, and a rule broken twice is promoted to a guard.

</td>
<td valign="top">

### 🧵 Never loses the thread
Right before the context is compacted, the brain saves your exact words, the open question and the files in flight. After compaction they come back verbatim, and `brain resume` continues in any agent.

</td>
<td valign="top">

### 📦 Starts smart
Lesson packs (safety, secrets, testing, git, python, typescript) give a fresh install real guards on day one. A **team brain** shares lessons through a git repo.

</td>
</tr>
<tr>
<td valign="top">

### 🤝 In every agent
Native hooks and a plugin for Claude Code, an MCP server for Cursor, Windsurf, Claude Desktop, VS Code, Gemini CLI and Codex, and instruction files for the rest.

</td>
<td valign="top">

### 🏠 Laptop only, plain Markdown
No server, no API key, no vector DB, zero dependencies. An ordinary Obsidian vault with `[[links]]`, diffable and yours.

</td>
<td valign="top">

### 🔒 Never behind your back
Nothing becomes a rule without your OK, and a lesson an agent wrote stays silent until you trust it. Secrets are blocked on save, on write and at commit.

</td>
</tr>
</table>

## 🚀 Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/ursalfas11/self-improving-brain/main/install.sh | sh
```

<p align="center">
  <img src="docs/assets/setup.svg" alt="brain setup in eight steps: vault, agents, the brain command, learning from past sessions, lesson packs, optional LLM judge, private GitHub backup, nightly run" width="92%">
</p>

No root, nothing to compile: git and Python 3.9+ are enough, and the `python3` that ships with macOS works. Step 4 reads the sessions you already had, so you get lesson candidates and a baseline on day one.

**Claude Code plugin**, if you prefer to install from inside Claude Code:

```text
/plugin install self-improving-brain --marketplace ursalfas11/self-improving-brain
```

The first session then offers to create your vault. Older Claude Code: `/plugin marketplace add ursalfas11/self-improving-brain`, then `/plugin install self-improving-brain@self-improving-brain`.

<details>
<summary>Other ways to install</summary>

```bash
pipx install git+https://github.com/ursalfas11/self-improving-brain && brain setup
# or, from a clone, no install at all:
git clone https://github.com/ursalfas11/self-improving-brain && cd self-improving-brain && ./brain setup
```
`brain setup --yes` accepts every default (it still creates nothing public). `--no-github`, `--no-llm`, `--no-schedule` and `--no-packs` skip those steps. Update any time with `brain update`.
</details>

<details>
<summary>What your agent sees at session start</summary>

```text
SELF-IMPROVING-BRAIN vault: ~/brain — before answering from memory, run `brain search "<question>"` …
LESSONS (apply in every session):
- L-result-first: Lead with the result in a few lines; details only when asked …
- L-verify-before-done: Before saying something works or is done, run it or test it and show the output …
BRAIN-REPO shop-api: shop-api — The shop-api codebase - layout, entry points, how to test
```
</details>

## 🔁 How it gets smarter

<p align="center">
  <img src="docs/assets/loop.svg" alt="The learning loop: you work, it learns, you approve, the right rule fires at the right moment, it measures" width="92%">
</p>

The model itself never learns. What improves is **what your agent is told, and when**, and the brain measures the effect:

<p align="center">
  <img src="docs/assets/stats.svg" alt="Example: mistakes per 100 messages fall from 12.4 to 5.3 over eight weeks (−57 %); recall precision rises from 41 % to 64 %" width="92%">
</p>

<details>
<summary><b>Every signal it learns from</b></summary>

<br>

| Signal | What the brain does |
|---|---|
| Recall offered a note, and the agent opened it | ✅ hit: the note gains weight |
| Recall offered a note, and the agent ignored it | the note loses weight; if precision drops, the **recall threshold rises by itself** |
| The agent opened a note recall *didn't* offer | the prompt's words get attached to that note, so it is found next time |
| You corrected the agent or rejected a tool call | 📝 lesson draft, refined by your own LLM CLI if you configured one |
| The agent wrote what a lesson's `check:` forbids | 🚨 flagged on the spot, counted per week; broken twice → *make it a guard* |
| A lesson was shown and the mistake came back anyway | 🔧 flagged: *sharpen the rule or make it a guard* |
| The mistake came back and the lesson wasn't shown | 🎯 flagged: *widen its triggers* (replay names the words) |
| The same commands ran in three or more sessions | 🧰 skill draft: the procedure, ready to approve |
| A lesson never fired in 30 days | 💤 flagged: *dormant* |
| A clear correction ("why did you…", "I told you…") or a lasting preference wasn't saved | 💾 at the end of that answer, the agent is asked **once** to store it with `brain remember`; weaker signals wait quietly for your review |

</details>

<details>
<summary><b>Under the hood: three layers, all local</b></summary>

<br>

1. **Inbox.** Anything new becomes one self-contained claim first (`fact`, `decision`, `pitfall`, `preference`, `todo`, `status`), never a note directly. It is secret-scanned and de-duplicated on entry.
2. **Wiki.** Markdown notes with a tiny frontmatter, hubs and `[[wikilinks]]`, kept to *one truth per fact*. `brain distill` works the inbox in, done by your agent or, with `llm_cmd`, by the nightly run. A patch that fails lint or the secret scan is rolled back, and a contradiction is never auto-resolved.
3. **Interface.** A CLI your agent calls instead of `cat`-ing whole files. `brain search` returns the 3–5 best *sections* with dates. `brain read note "section"` returns an outline with token counts, or exactly one section.

Recall combines **BM25 over sections**, **learned terms** and **graph spreading** over your `[[links]]`. It is explainable and has zero dependencies. The parsed vault is cached and rebuilt only when a note changes, so the hooks stay fast:

| Hook (600-note vault, Apple Silicon, incl. Python start) | Time |
|---|---|
| session start | ~60 ms |
| every prompt (recall + word lessons) | ~70 ms |
| every tool call (reflexes, guards, secret block) | ~50 ms |

The details are in [docs/architecture.md](docs/architecture.md).
</details>

## 🔬 Prove it

Most memory tools store things and hope. This one shows its work, on your own history:

```text
$ brain replay                                    # example output
REPLAY · 127 sessions · last 30 days · 24 active lessons
  Your lessons would have caught 31 of 64 corrections (48 %) — 7 of them by asking you first.
  (caught = a lesson about this mistake would have fired before it happened)
  missed 9 (a lesson exists, its triggers did not fire) · uncovered 24 (no lesson yet)
LESSONS
  L-testing-no-retry-flaky                 caught   6 · missed   2  → widen: words race, timeout
```

`brain replay --draft` turns the uncovered ones into lesson drafts. Nothing leaves your machine, and no LLM is involved.

<p align="center">
  <img src="docs/assets/report.svg" alt="Example report card: corrections per 100 messages down 61 % over 12 weeks; 20 lessons; 48 % of past mistakes caught; 7 risky actions flagged" width="80%">
</p>

`brain report --public` draws this card from your numbers: no notes, no rules, no quotes, no paths, so you can post it. `brain report --badge` makes a small README badge. (The card above uses the example data of `brain ui --demo`.)

**Brain-Bench** goes one step further, with real agent runs: eight small repos that tempt an agent into a known mistake (a retry on a flaky test, skipping a red test, a hard-coded key, a bare `except`, force-pushing over a colleague, `rm -rf`, committing `.env`, a mutable default). [`bench/run.py`](bench/README.md) runs `claude -p` on each, with and without the brain, fully isolated, and scores what the agent ran and left behind. Without `--run` it only prints the plan and a cost ceiling, so you decide what your quota pays for.

## 🖥️ See it

```bash
brain ui          # your brain in the browser: progress, lessons, review, replay, graph
brain ui --demo   # the same with example data
```

<p align="center">
  <img src="docs/assets/ui.png" alt="brain ui: corrections per 100 messages falling week by week, replay result, lessons by mechanism" width="92%">
</p>

The page is served on 127.0.0.1 only and answers only requests that carry the random token from the link `brain ui` prints, so no web page you visit can read your vault or click *approve* for you.

## 🎯 Lessons

A lesson is a callout **in the note that owns the topic**, so there is no separate rules file that drifts.

```markdown
> [!lesson]- L-fix-flaky-tests-root
> rule: Fix flaky tests at the root (race, shared state); never paper over them with retries or sleeps.
> mechanism: reflex
> words: flaky, is flaky, retry
> paths: (^|/)tests?/|test_
> check: @retry\b|pytest\.mark\.flaky
> since: 2026-10-28
```

| Mechanism | Effect |
|---|---|
| `always` | in every session start (max 8) |
| `reflex` | once per session when a trigger matches: the normal case |
| `guard` | the tool call needs your OK: force-push, `rm -rf`, deploys. In headless runs `BRAIN_GUARD=deny` refuses instead |
| `skill` | like reflex; the rule points at a procedure the brain learned (`brain skills`) |

**Triggers:** `words` (two hits, or one exact phrase) · `tools` (a name suffix: `deploy` matches any `mcp__*__deploy`) · `commands` (a regex on the shell command) · `paths` (a regex on file paths). **`check:`** is a regex on what the agent writes or runs: a match on a reflex is a violation the brain counts on its own; on a guard it asks before the write happens. `brain promote <id> --to guard` escalates a rule that keeps getting broken.

**Packs.** `brain pack list` shows ready-made lessons, `brain pack add safety` installs one into your vault, where it is yours to edit. `brain pack export` turns your own lessons into a pack to share; evidence and dates are dropped and it is secret-scanned first.

**Team brain.** `brain team add acme <git-url>` shares lessons through a plain git repo. A team lesson counts only in the form you accepted: whatever the team changes later waits for `brain team sync` to show it to you. `brain team propose <id>` offers one of yours on a branch.

**Trust.** A lesson is plain text, so anything that can write a file could plant one, including an agent that read a poisoned web page. A lesson that appears during an agent's tool call stays silent until you run `brain trust`. Approving, trusting, promoting and adding packs or teams are commands the agent has to ask you for.

## 🧰 Knows your work

| | |
|---|---|
| `brain skills` | Procedures your agent repeated in three or more sessions (a release, a deploy, how this project is tested) become skill drafts. Approve one and it becomes a vault note, a `skill` lesson and a Claude Code skill. |
| `brain atlas` | A map of a codebase in its repo note: languages, layout, entry points and scripts, the test command, CI workflows, one line per key module. The session start says when the repo has moved on. |
| `brain ingest <file\|url\|folder>` | Sources become notes. `brain ingest --refresh` fetches them again; when one changed, every note that links to it is flagged for a check, so knowledge built on an old page does not go stale quietly. |
| `brain resume` | The last handoff: your own words, the open question, the files in flight. Works across agents: Codex can pick up where Claude stopped. |

## 🤝 Works with every agent

| Agent | Integration | You get |
|---|---|---|
| <img src="https://img.shields.io/badge/-Claude%20Code-D97757?logo=anthropic&logoColor=white" alt="Claude Code"> | **native hooks** or the **plugin**, + `/brain-review` `/brain-distill` `/brain-ingest` `/brain-ask` | everything: recall, reflexes, guards, checks, secret block, handoff across compaction, auto-learn at session end |
| <img src="https://img.shields.io/badge/-Cursor%20·%20Windsurf%20·%20Claude%20Desktop%20·%20VS%20Code-555555" alt="MCP clients"> | **MCP server**: `brain mcp install <client>` | `brain_context`, `brain_check`, `brain_correction`, `brain_search`, `brain_handoff` and more; none of them can approve a rule |
| <img src="https://img.shields.io/badge/-Codex%20CLI-000000?logo=openai&logoColor=white" alt="Codex CLI"> | `~/.codex/AGENTS.md`, MCP, transcript learning | context, lessons, guards via `brain check`, plus mistakes mined from `~/.codex/sessions` |
| <img src="https://img.shields.io/badge/-Gemini%20CLI-4285F4?logo=google&logoColor=white" alt="Gemini CLI"> | `~/.gemini/GEMINI.md` + MCP | context, search, remember, lessons, guards |
| <img src="https://img.shields.io/badge/-Grok%20·%20Aider%20·%20Zed%20·%20Cline-555555" alt="Others"> | `brain install agents` → the project's `AGENTS.md`, or `brain mcp config` | context, search, remember, lessons |
| anything with a shell | `brain install print` | paste the block wherever your agent reads instructions |

## 🌙 Autonomous, on your laptop

Every night (launchd, cron or Task Scheduler), the brain runs one pass and backs itself up to your **private** repo:

```text
learn → distill* → mine skills → replay → refresh recall model → autotune threshold → lesson health → INDEX.md → lint → git sync
```

\*To let it think on its own, point it at an LLM CLI you already use. It never holds an API key:

```bash
brain config llm_cmd "claude -p --model haiku"     # or "codex exec -", or "ollama run llama3.1"
```

<details>
<summary><b>📋 All commands</b></summary>

<br>

```text
brain setup                     guided install            brain stats                is it getting smarter?
brain context                   session-start context     brain replay [--draft]     what would today's lessons have caught?
brain search "<question>"       best sections, dated      brain report [--public]    report card, shareable SVG, badge
brain read <note> [section]     outline or one section    brain ui [--demo]          the brain in your browser
brain neighbors <note>          linked notes              brain lessons              list all lessons
brain remember <type> "<claim>" into the inbox            brain lesson "<rule>" …    add a lesson by hand
brain inbox [done|drop <id>]    manage the inbox          brain promote <id> --to …  e.g. reflex → guard
brain distill [--auto]          inbox → wiki              brain trust [<id>]         lessons that stay silent
brain learn                     mine transcripts          brain check --command …    would this trip a guard?
brain review                    lesson drafts             brain pack list|add|export lesson packs
brain approve <id> --rule …     draft → lesson            brain team add|sync|propose team brain
brain reject <id> [--block …]   discard, block topic      brain skills [mine|approve] learned procedures
brain resume / brain handoff    continue a session        brain atlas [path]         map a codebase
brain ingest <src> [--refresh]  sources → notes           brain mcp [install <client>] MCP server
brain lint · index · sync       upkeep                    brain nightly · schedule   the nightly loop
brain config [key value]        settings                  brain install <agent>      connect an agent
brain doctor                    check the setup           brain scan [--staged]      secret scan
```

</details>

<details>
<summary><b>🗂️ Your vault</b></summary>

<br>

```text
~/brain/
├── INDEX.md          generated map: one line per note
├── 00-hubs/          entry points, one per topic, carry the current state
├── 10-projects/      things with an end, and one note per repo (with its atlas)
├── 20-areas/         lasting knowledge: decisions, how-tos, pitfalls, preferences
├── 30-resources/     references: sources, packs, skills
├── 90-entities/      short stubs for systems, tools, people
└── .brain/           inbox, drafts, events, trust ledger (versioned) · state, team clones (local only)
```

Open it in Obsidian and you get the graph for free: hubs as cluster centres, notes around them.

</details>

## 🆚 Compared to the usual

| | `CLAUDE.md` / `AGENTS.md` by hand | typical memory servers | **self-improving-brain** |
|---|:-:|:-:|:-:|
| Learns from your corrections automatically | ✗ | rarely | ✅ |
| Injects a rule *at the moment* it matters | ✗ | on request | ✅ |
| Can stop an irreversible action | ✗ | ✗ | ✅ |
| Notices a broken rule without you | ✗ | ✗ | ✅ |
| Replays your history to show what it would have caught | ✗ | ✗ | ✅ |
| Keeps your exact words across compaction | ✗ | ✗ | ✅ |
| A planted rule stays silent until you trust it | ✗ | ✗ | ✅ |
| Context cost per session | grows forever | tool schemas | **one line** (hooks) |
| Runs on | – | a server / cloud | **your laptop** |
| Format | one big file | a database | **Markdown + links** |

## ❓ FAQ

<details>
<summary><b>Does the model itself learn?</b></summary>

No, and nothing running on your laptop can do that. What improves is *what the agent is told, and when*. You can see the improvement: `brain stats` shows corrections per 100 messages, week by week, and `brain replay` shows what your current lessons would have caught.
</details>

<details>
<summary><b>How accurate is the mistake detection?</b></summary>

We tested it on 127 real sessions with 1,664 user messages. Pattern matching found 64 corrections. In a sample, roughly half of the candidates were real, reusable mistakes; the rest were one-off steering ("no, the other file"). That is why nothing becomes a lesson on its own: the optional LLM judge (`llm_cmd`), `brain ui` or your `/brain-review` sorts the candidates, and the strongest, most repeated ones come first. Bare answers to the agent's own questions ("no, just the new one") and accidental rejections ("sorry, go on") are filtered out before you ever see them.
</details>

<details>
<summary><b>What does "caught" in replay mean?</b></summary>

A lesson about that mistake (its words, command or check match what happened) would have fired before the agent made it: in the prompt that started the turn, or at a tool call before your correction. It does not claim the agent would then have listened; that is what `brain stats` (did the mistake come back after the lesson was shown?) and Brain-Bench measure.
</details>

<details>
<summary><b>CLI or MCP server?</b></summary>

Both. In Claude Code the brain uses hooks and a CLI: the hooks add one line at session start and fire lessons only when they match, and MCP tool schemas would cost tokens in every request. Clients without hooks get the MCP server (`brain mcp install cursor`), whose instructions tell the agent to call `brain_context` first and `brain_check` before irreversible commands.
</details>

<details>
<summary><b>Could someone plant a lesson in my brain?</b></summary>

That is the risk we designed against. A lesson that appears or changes during an agent's tool call is recorded as pending in the trust ledger and never reaches the agent until you run `brain trust`. Team and pack lessons count only in the exact form you accepted. Approving, trusting and promoting are commands the agent cannot run without asking you, and writes into `.brain/` are refused. `trust_mode strict` goes further: only lessons created through the brain count.
</details>

<details>
<summary><b>Why no embeddings?</b></summary>

BM25 + learned terms + the link graph is fast, explainable and has zero dependencies. The learned terms close exactly the vocabulary gaps that embeddings are usually brought in for. A pluggable, still-local embedding scorer is on the roadmap.
</details>

<details>
<summary><b>Can I use my existing notes?</b></summary>

Yes: `brain init ~/notes --force`. It adds `.brain/`, a generated `INDEX.md` and a few starter notes, and never overwrites an existing file. That includes your own `INDEX.md` and git pre-commit hook. Edits to existing notes only append text and set the `updated:` line; the rest of your frontmatter stays byte-for-byte.
</details>

<details>
<summary><b>What if my laptop dies? Multiple machines?</b></summary>

`brain setup` offers to create a **private** GitHub repo for your brain (with the `gh` CLI), or shows you the one git command to use any other private remote. The nightly run commits, pulls and pushes, and it refuses to push to a repo that turned out to be public. On a new machine: clone your brain repo, then `brain setup --vault <that folder>` and `brain skills sync`. Every machine writes its own event file, so there are no merge conflicts, and every laptop learns from all the others.
</details>

## 🗺️ Roadmap

- [x] MCP server for shell-less clients
- [x] local web UI for reviewing drafts
- [x] replay past sessions against today's lessons
- [ ] Brain-Bench results across models, published in `bench/results`
- [ ] native hooks for Codex CLI and Gemini CLI
- [ ] transcript parsers for Cursor, Aider, Grok CLI
- [ ] pluggable local embedding scorer

## 💜 Contributing

Issues and PRs are very welcome, above all adapters for more agents, new lesson packs and Brain-Bench tasks. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md). To run the tests: `python3 -m unittest discover -s tests`. The README images are built by `python3 docs/assets/src/build.py`.

<div align="center">

<br>

**If this brain saved you from repeating a correction, a ⭐ helps other people find it.**

<a href="https://star-history.com/#ursalfas11/self-improving-brain&Date">
  <img src="https://api.star-history.com/svg?repos=ursalfas11/self-improving-brain&type=Date" alt="Star history" width="560">
</a>

<sub>MIT licensed · built for agents that should get better, not just bigger</sub>

</div>
