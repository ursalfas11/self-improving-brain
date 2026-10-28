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

Most agent-memory tools store **facts** and hope the model looks them up. But most agent mistakes don't come from missing facts. They come from a rule the agent *knew* and didn't apply **at the moment it mattered**. self-improving-brain works on exactly that moment, and then it checks whether the mistake ever comes back.

<table>
<tr>
<td width="33%" valign="top">

### 🧠 Learns from you
`brain learn` reads your Claude Code and Codex transcripts. It finds every *"why did you…"*, every rejected tool call and every *"from now on…"*. Answers to the agent's own questions and accidental rejections are skipped, and repeats are grouped. You approve each lesson in one line.

</td>
<td width="33%" valign="top">

### ⚡ Fires at the right moment
A lesson is shown when its trigger matches: prompt words, a tool, a shell command or a file path. A `guard` lesson makes the agent **ask you** before `git push --force`, `rm -rf` or a deploy.

</td>
<td width="33%" valign="top">

### 📉 Proves it works
`brain stats` shows mistakes per 100 messages week by week, whether each lesson fired and whether the mistake came back anyway. Recall tunes its own threshold from what the agent really opens.

</td>
</tr>
<tr>
<td valign="top">

### 🏠 Laptop only
No server, no API key, no vector DB, no dependencies; it needs only Python 3.9+ (the `python3` that ships with macOS is enough). A nightly job runs via launchd, cron or Task Scheduler. Your brain is backed up to a **private** git repo, so a dead laptop never takes it with it.

</td>
<td valign="top">

### 📝 Plain Markdown
Your brain is an ordinary Obsidian vault: notes, hubs and `[[links]]`, all of it diffable and yours. Lessons live **inside** the note that owns the topic.

</td>
<td valign="top">

### 🔒 Never behind your back
Nothing becomes a lesson without your OK, and nothing is deleted automatically. Secrets are blocked at three points: when you save one, when the agent writes one, and at git commit.

</td>
</tr>
</table>

## 🚀 Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/ursalfas11/self-improving-brain/main/install.sh | sh
```

One line, no root, nothing to compile. You need git and Python 3.9+; the `python3` that ships with macOS works. The installer starts `brain setup`, which walks you through seven short questions:

```text
1/7  Your vault                      ~/brain, with its own git history and a secret-scan pre-commit hook
2/7  Connect your agents             finds Claude Code, Codex and Gemini CLI on this machine
3/7  The `brain` command             links it into ~/.local/bin
4/7  Learn from past sessions        "127 sessions · 1,664 of your messages · 64 corrections → 59 lesson candidates"
5/7  Smarter judging (optional)      uses a CLI you already have (claude, codex, ollama), no API key stored
6/7  Backup                          creates a PRIVATE GitHub repo for your brain, so a dead laptop never kills it
7/7  Nightly self-improvement        launchd (macOS), cron (Linux) or Task Scheduler (Windows)
```

Step 4 gives you value on day one: the brain reads the sessions you already had, finds the mistakes you corrected and gives you a baseline for `brain stats`.

<details>
<summary>Other ways to install</summary>

```bash
pipx install git+https://github.com/ursalfas11/self-improving-brain && brain setup
# or, from a clone, no install at all:
git clone https://github.com/ursalfas11/self-improving-brain && cd self-improving-brain && ./brain setup
```
`brain setup --yes` accepts every default (it still creates nothing public). `--no-github`, `--no-llm` and `--no-schedule` skip those steps. Update any time with `brain update`.
</details>

Your next agent session starts with:

```text
SELF-IMPROVING-BRAIN vault: ~/brain — before answering from memory, run `brain search "<question>"` …
LESSONS (apply in every session):
- L-result-first: Lead with the result in a few lines; details only when asked …
- L-verify-before-done: Before saying something works or is done, run it or test it and show the output …
```

## 🔁 How it gets smarter

<p align="center">
  <img src="docs/assets/loop.svg" alt="The learning loop: you work, it learns, you approve, the right rule fires at the right moment, it measures" width="92%">
</p>

The model itself never learns. What improves is **what your agent is told, and when**. Every signal below is logged locally, and the nightly run acts on it:

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

```text
$ brain stats
RECALL (30 days)
  prompts 412 · injections 96 · opened 61 · found without recall 14 · precision 0.64 · threshold 4.6
CORRECTIONS per 100 user messages (lower = smarter agent)
  2026-W41   12.4  ████████████  (31/250)
  2026-W42    9.8  █████████  (22/224)
  2026-W43    7.1  ███████  (17/239)
  2026-W44    5.3  █████  (11/208)
LESSONS
  L-fix-flaky-tests-root         reflex  shown  11 · ignored 0 · missed 0  → ok
  L-ask-before-force-push        guard   shown   3 · ignored 0 · missed 0  → ok
  L-use-pnpm-not-npm             reflex  shown   7 · ignored 2 · missed 0  → sharpen rule or use guard
```
<sub>Example output. Your numbers come from your own sessions.</sub>

### Three layers, all local

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

## 🎯 Lessons

A lesson is a callout **in the note that owns the topic**. There is no separate rules file that drifts.

```markdown
> [!lesson]- L-fix-flaky-tests-root
> rule: Fix flaky tests at the root (race, shared state); never paper over them with retries or sleeps.
> mechanism: reflex
> words: flaky, test, retry
> since: 2026-10-28
```

| Mechanism | Effect | Use for |
|---|---|---|
| `always` | in every session start (max 8) | rules that apply everywhere |
| `reflex` | once per session when a trigger matches | the normal case |
| `guard` | the tool call needs your OK | irreversible actions: force-push, `rm -rf`, deploys |
| `skill` | like reflex; the rule names a playbook to load | whole procedures |

**Triggers**

- `words`: two hits, or one exact phrase. A single common word never fires.
- `tools`: the tool-name suffix, so `media_upload` matches any `mcp__*__media_upload`.
- `commands`: a regex on the shell command head; heredoc bodies are ignored.
- `paths`: a regex on file paths.

## 🤝 Works with every agent

| Agent | Integration | You get |
|---|---|---|
| <img src="https://img.shields.io/badge/-Claude%20Code-D97757?logo=anthropic&logoColor=white" alt="Claude Code"> | **native hooks** + `/brain-review` `/brain-distill` `/brain-ingest` `/brain-ask` | everything: recall, reflexes, guards, secret block, learning signal, auto-learn at session end |
| <img src="https://img.shields.io/badge/-Codex%20CLI-000000?logo=openai&logoColor=white" alt="Codex CLI"> | `~/.codex/AGENTS.md` + transcript learning | context, search, remember and lessons, plus mistakes mined from `~/.codex/sessions` |
| <img src="https://img.shields.io/badge/-Gemini%20CLI-4285F4?logo=google&logoColor=white" alt="Gemini CLI"> | `~/.gemini/GEMINI.md` | context, search, remember, lessons |
| <img src="https://img.shields.io/badge/-Cursor%20·%20Grok%20·%20Aider%20·%20Zed%20·%20Cline-555555" alt="Others"> | `brain install agents` → the project's `AGENTS.md` | context, search, remember, lessons |
| anything with a shell | `brain install print` | paste the block wherever your agent reads instructions |

## 🌙 Autonomous, on your laptop

`brain schedule install` registers a nightly job: **launchd** on macOS, which runs a missed job when the Mac wakes up, or **cron** on Linux. Every run goes through these steps:

```text
learn → distill* → refresh recall model → autotune threshold → lesson health → INDEX.md → lint → git sync
```

\*Want it to think on its own? Point it at the LLM CLI you already use. self-improving-brain never holds an API key:

```bash
brain config llm_cmd "claude -p --model haiku"     # or "codex exec -", or "ollama run llama3.1"
```

Without `llm_cmd`, your agent does the thinking the next time you run `/brain-review`.

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

Issues and PRs are very welcome, above all adapters for more agents. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md). To run the tests: `python3 -m unittest discover -s tests`.

<div align="center">

<br>

**If this brain saved you from repeating a correction, a ⭐ helps other people find it.**

<a href="https://star-history.com/#ursalfas11/self-improving-brain&Date">
  <img src="https://api.star-history.com/svg?repos=ursalfas11/self-improving-brain&type=Date" alt="Star history" width="560">
</a>

<sub>MIT licensed · built for agents that should get better, not just bigger</sub>

</div>
