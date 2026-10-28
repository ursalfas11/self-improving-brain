<div align="center">

<img src="docs/assets/banner.svg" alt="self-improving-brain: your coding agent forgets everything between sessions. This brain gets smarter with every one." width="100%">

<br>

<h3>The self-improving memory &amp; second brain for AI coding agents</h3>

<p>
It turns your corrections into rules, injects the right rule at the right moment,<br>
and <b>measures whether your agent actually makes fewer mistakes</b>, week after week.
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
`brain learn` turns every *"no, why did you…"* and every rejected tool call in your transcripts into a lesson draft. You OK it in one line.

</td>
<td width="33%" valign="top">

### ⚡ Fires at the right moment
Prompt words, a tool, a shell command or a path trigger it. A `guard` makes the agent **ask you** before `git push --force`.

</td>
<td width="33%" valign="top">

### 📉 Proves it works
`brain stats` shows mistakes per 100 messages week by week, and whether each lesson fired.

</td>
</tr>
<tr>
<td valign="top">

### 🏠 Laptop only
No server, no API key, no vector DB, zero dependencies. Python 3.9+ is all it needs.

</td>
<td valign="top">

### 📝 Plain Markdown
An ordinary Obsidian vault with `[[links]]`. Diffable, and yours.

</td>
<td valign="top">

### 🔒 Never behind your back
Nothing becomes a lesson without your OK. Secrets are blocked on save, on write and at commit.

</td>
</tr>
</table>

## 🚀 Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/ursalfas11/self-improving-brain/main/install.sh | sh
```

<p align="center">
  <img src="docs/assets/setup.svg" alt="brain setup in seven steps: vault, agents, the brain command, learning from past sessions, optional LLM judge, private GitHub backup, nightly run" width="92%">
</p>

No root, nothing to compile: git and Python 3.9+ are enough, and the `python3` that ships with macOS works. Step 4 reads the sessions you already had, so you get lesson candidates and a baseline on day one.

<details>
<summary>Other ways to install</summary>

```bash
pipx install git+https://github.com/ursalfas11/self-improving-brain && brain setup
# or, from a clone, no install at all:
git clone https://github.com/ursalfas11/self-improving-brain && cd self-improving-brain && ./brain setup
```
`brain setup --yes` accepts every default (it still creates nothing public). `--no-github`, `--no-llm` and `--no-schedule` skip those steps. Update any time with `brain update`.
</details>

<details>
<summary>What your agent sees at session start</summary>

```text
SELF-IMPROVING-BRAIN vault: ~/brain — before answering from memory, run `brain search "<question>"` …
LESSONS (apply in every session):
- L-result-first: Lead with the result in a few lines; details only when asked …
- L-verify-before-done: Before saying something works or is done, run it or test it and show the output …
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
| A lesson was shown and the mistake came back anyway | 🔧 flagged: *sharpen the rule or make it a guard* |
| The mistake came back and the lesson wasn't shown | 🎯 flagged: *widen its triggers* |
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

## 🎯 Lessons

A lesson is a callout **in the note that owns the topic**, so there is no separate rules file that drifts.

```markdown
> [!lesson]- L-fix-flaky-tests-root
> rule: Fix flaky tests at the root (race, shared state); never paper over them with retries or sleeps.
> mechanism: reflex
> words: flaky, test, retry
> since: 2026-10-28
```

| Mechanism | Effect |
|---|---|
| `always` | in every session start (max 8) |
| `reflex` | once per session when a trigger matches: the normal case |
| `guard` | the tool call needs your OK: force-push, `rm -rf`, deploys |
| `skill` | like reflex; the rule names a playbook to load |

**Triggers:** `words` (two hits, or one exact phrase) · `tools` (a name suffix: `deploy` matches any `mcp__*__deploy`) · `commands` (a regex on the shell command) · `paths` (a regex on file paths).

## 🤝 Works with every agent

| Agent | Integration | You get |
|---|---|---|
| <img src="https://img.shields.io/badge/-Claude%20Code-D97757?logo=anthropic&logoColor=white" alt="Claude Code"> | **native hooks** + `/brain-review` `/brain-distill` `/brain-ingest` `/brain-ask` | everything: recall, reflexes, guards, secret block, learning signal, auto-learn at session end |
| <img src="https://img.shields.io/badge/-Codex%20CLI-000000?logo=openai&logoColor=white" alt="Codex CLI"> | `~/.codex/AGENTS.md` + transcript learning | context, search, remember and lessons, plus mistakes mined from `~/.codex/sessions` |
| <img src="https://img.shields.io/badge/-Gemini%20CLI-4285F4?logo=google&logoColor=white" alt="Gemini CLI"> | `~/.gemini/GEMINI.md` | context, search, remember, lessons |
| <img src="https://img.shields.io/badge/-Cursor%20·%20Grok%20·%20Aider%20·%20Zed%20·%20Cline-555555" alt="Others"> | `brain install agents` → the project's `AGENTS.md` | context, search, remember, lessons |
| anything with a shell | `brain install print` | paste the block wherever your agent reads instructions |

## 🌙 Autonomous, on your laptop

Every night (launchd, cron or Task Scheduler), the brain runs one pass and backs itself up to your **private** repo:

```text
learn → distill* → refresh recall model → autotune threshold → lesson health → INDEX.md → lint → git sync
```

\*To let it think on its own, point it at an LLM CLI you already use. It never holds an API key:

```bash
brain config llm_cmd "claude -p --model haiku"     # or "codex exec -", or "ollama run llama3.1"
```

<details>
<summary><b>📋 All commands</b></summary>

<br>

```text
brain init [path] [--git]       create a vault            brain lessons              list all lessons
brain context                   session-start context     brain lesson "<rule>" …    add a lesson by hand
brain search "<question>"       best sections, dated      brain stats                is it getting smarter?
brain read <note> [section]     outline or one section    brain lint                 dead links, secrets, dupes, stale
brain neighbors <note>          linked notes              brain index                regenerate INDEX.md
brain remember <type> "<claim>" into the inbox            brain nightly              run the loop once
brain inbox [done|drop <id>]    manage the inbox          brain schedule install     run it every night
brain distill [--auto]          inbox → wiki              brain sync                 git commit/pull/push
brain learn                     mine transcripts          brain config [key value]   settings
brain review                    lesson drafts             brain install <agent>      connect an agent
brain approve <id> --rule …     draft → lesson            brain doctor               check the setup
brain reject <id> [--block …]   discard, block topic      brain scan [--staged]      secret scan
```

</details>

<details>
<summary><b>🗂️ Your vault</b></summary>

<br>

```text
~/brain/
├── INDEX.md          generated map: one line per note
├── 00-hubs/          entry points, one per topic, carry the current state
├── 10-projects/      things with an end
├── 20-areas/         lasting knowledge: decisions, how-tos, pitfalls, preferences
├── 30-resources/     references
├── 90-entities/      short stubs for systems, tools, people
└── .brain/           inbox, drafts, events (versioned) · state (local only)
```

Open it in Obsidian and you get the graph for free: hubs as cluster centres, notes around them.

</details>

## 🆚 Compared to the usual

| | `CLAUDE.md` / `AGENTS.md` by hand | typical memory servers | **self-improving-brain** |
|---|:-:|:-:|:-:|
| Learns from your corrections automatically | ✗ | rarely | ✅ |
| Injects a rule *at the moment* it matters | ✗ | on request | ✅ |
| Can stop an irreversible action | ✗ | ✗ | ✅ |
| Proves it's getting better | ✗ | ✗ | ✅ |
| Recall learns what you actually need | ✗ | embeddings | ✅ |
| Context cost per session | grows forever | tool schemas | **one line** |
| Runs on | – | a server / cloud | **your laptop** |
| Format | one big file | a database | **Markdown + links** |

## ❓ FAQ

<details>
<summary><b>Does the model itself learn?</b></summary>

No, and nothing running on your laptop can do that. What improves is *what the agent is told, and when*. You can see the improvement: `brain stats` shows corrections per 100 messages, week by week.
</details>

<details>
<summary><b>How accurate is the mistake detection?</b></summary>

We tested it on 127 real sessions with 1,664 user messages. Pattern matching found 64 corrections. In a sample, roughly half of the candidates were real, reusable mistakes; the rest were one-off steering ("no, the other file"). That is why nothing becomes a lesson on its own: the optional LLM judge (`llm_cmd`) or your `/brain-review` sorts the candidates, and the strongest, most repeated ones come first. Bare answers to the agent's own questions ("no, just the new one") and accidental rejections ("sorry, go on") are filtered out before you ever see them.
</details>

<details>
<summary><b>Why a CLI and not an MCP server?</b></summary>

MCP tool schemas cost tokens in every session; a CLI costs one line of instructions. Most of the savings come from reading one section instead of a whole file, and that is the engine's job, not the protocol's. A thin MCP wrapper for shell-less clients is welcome as a contribution.
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

`brain setup` offers to create a **private** GitHub repo for your brain (with the `gh` CLI), or shows you the one git command to use any other private remote. The nightly run commits, pulls and pushes, and it refuses to push to a repo that turned out to be public. On a new machine: clone your brain repo, then `brain setup --vault <that folder>`. Every machine writes its own event file, so there are no merge conflicts, and every laptop learns from all the others.
</details>

## 🗺️ Roadmap

- [ ] native hooks for Codex CLI and Gemini CLI
- [ ] transcript parsers for Cursor, Aider, Grok CLI
- [ ] optional MCP wrapper for shell-less clients
- [ ] pluggable local embedding scorer
- [ ] `brain eval`: replay labelled sessions to benchmark recall and lesson precision
- [ ] tiny local web UI for reviewing drafts

## 💜 Contributing

Issues and PRs are very welcome, above all adapters for more agents. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md). To run the tests: `python3 -m unittest discover -s tests`. The README images are built by `python3 docs/assets/src/build.py`.

<div align="center">

<br>

**If this brain saved you from repeating a correction, a ⭐ helps other people find it.**

<a href="https://star-history.com/#ursalfas11/self-improving-brain&Date">
  <img src="https://api.star-history.com/svg?repos=ursalfas11/self-improving-brain&type=Date" alt="Star history" width="560">
</a>

<sub>MIT licensed · built for agents that should get better, not just bigger</sub>

</div>
