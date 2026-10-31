# Architecture

self-improving-brain is ~6.6k lines of standard-library Python (3.9+). This page explains the moving parts so you
can change them with confidence.

## Design principles

1. **Rules at the moment beat facts on request.** Most agent mistakes are not missing knowledge
   but known rules that weren't applied. So a lesson is a single imperative sentence injected
   when its trigger matches, not a pointer to a note.
2. **Measure, then tune.** Every injection and every read is logged. Recall weights, the recall
   threshold and lesson health all come from those numbers, not from guesses.
3. **Autonomous, with a veto.** The nightly run learns, files and cleans up on its own, by fixed rules.
   Every change it makes is journaled with what it takes to undo it. Nothing waits for approval, and
   nothing changes without a record.
4. **One truth per fact.** A fact lives in exactly one note, and a newer statement replaces the outdated
   one. Lessons live inside the note that owns their topic. Duplicates are linted.
5. **Cheap by default.** Each prompt gets at most two one-line recalls, each lesson fires once per
   session, and `always` lessons are capped at 8. The CLI returns sections, not files. The nightly LLM
   has a hard daily budget, and the brain measures its own share of the agent's tokens.
6. **Fail open.** A hook that crashes outputs nothing. The agent is never blocked by a broken brain.
   It blocks on purpose only for secrets, writes into `.brain/`, and guards, which ask you rather than refuse.
7. **Only your words become rules.** The nightly run learns from what the user typed, never from tool
   output, never from what an agent reports over MCP, and never adopts text with a link or a secret. A lesson
   that appeared during a tool call stays silent until you trust it, and the commands that change rules,
   trust or config need your OK when an agent runs them.

## Modules

| module | job |
|---|---|
| `notes.py` | parse Markdown: frontmatter subset, H2 sections, `[[links]]`, `[!lesson]` callouts (code blocks ignored) |
| `search.py` | BM25 over sections + learned terms + hit-rate multiplier + graph spreading |
| `events.py` | append-only per-host event log; per-session "already shown" state |
| `inbox.py` | single-claim queue with secret scan and near-duplicate check |
| `transcripts.py` | Claude Code / Codex / generic JSONL → neutral `Turn`s |
| `learn.py` | correction & preference mining (no LLM), the nightly judge, the adoption policy, sharpen and escalate, lesson writing |
| `extract.py` | decisions, facts, pitfalls and preferences from new sessions → inbox claims |
| `distill.py` | inbox → wiki: append, replace the outdated text, or a new note; lint + rollback; journaled |
| `curate.py` | retire quiet lessons, demote over the `always` cap, keep under the lesson cap |
| `journal.py` | every autonomous change, per host and append-only; `brain review` and `brain undo` |
| `llm.py` | the LLM CLI (claude, codex, ollama): headless call, JSON answer, cost booking, daily budget |
| `metrics.py` | learned recall model, weekly correction curve, token share, weekly digest, lesson health, threshold autotune |
| `hooks.py` | Claude Code hook protocol: session-start (one line, `always` lessons, repo notes, handoff, the weekly line), prompt, pre-tool (guards, `check:` on writes), post-tool (`check:` violations), pre-compact, session-end |
| `cache.py` | parsed vault + BM25 index cached in `~/.cache/self-improving-brain/`, keyed by a fingerprint of every note's size and mtime plus the code version |
| `setup.py` | `brain setup` without questions: vault, agents, PATH, history baseline, LLM CLI, nightly schedule, `--github` backup |
| `adapters.py` | install/uninstall into Claude Code, Codex, Gemini, project `AGENTS.md`; the plugin's hooks and commands |
| `nightly.py` | the nightly pass with a lock, its launchd/cron/Task Scheduler entry, the catch-up check, git pull and sync |
| `ledger.py` | records several machines change (drafts, inbox): one file per machine, merged on read |
| `lint.py` | MUST: secrets, dead links, duplicate lesson ids. SHOULD: orphans, stale projects, near-duplicates, trigger-less lessons |
| `trust.py` | the trust ledger: which lessons count, in which exact form (fingerprint), and which wait for `brain trust` |
| `handoff.py` | PreCompact handoff: your last words verbatim, the open question, files in flight; `brain resume` |
| `replay.py` | past corrections against today's lessons: caught, missed, uncovered, per lesson |
| `report.py` | report card and badge as SVG, numbers only |
| `mcp.py` | MCP stdio server (JSON-RPC 2.0) and client config install for Cursor, Windsurf, Claude Desktop, VS Code, Gemini, Codex |
| `ui.py` | `brain ui`: a local page (`ui.html`) and a small JSON API on 127.0.0.1: Overview, Learned (undo), Rules, Replay, Map |

## Recall score

For a prompt `q`:

```
text(note)    = max_section BM25(q, section) + 0.2 · second best section
learned(note) = Σ_{t ∈ q ∩ learned_terms(note)} idf(t) · 0.8 · min(count, 3)
hitrate(note) = 0.75 + 0.5 · (hits + 1) / (shown + 2)        (only once shown ≥ 3)
score         = (text + learned) · hitrate
graph         = top-3 seeds pass β · score / √(deg(seed) · deg(neighbour)) to linked notes (β = 0.3)
```

Hubs are excluded from the graph and from prompt recall, because they link everything and would
always win. A note is injected only if its score is ≥ `recall_threshold`. The nightly run raises
the threshold by 15 % when precision falls below 35 %, and lowers it by 10 % when precision is above
70 % and recall rarely fires.

## Events

```
recall      {session, notes[], terms[], scores[]}
read        {session, note, via: cli|read}
lesson      {session, id, trigger}
correction  {session, ts, week, text, lessons_shown[], draft}
turns       {session, source, user, corrections, week, tokens}
distill     {item, action, note}
violation   {session, id, tool, sample}           a lesson's check: matched what the agent wrote or ran
handoff     {session, repo}
error       {where, msg}
```

Next to the events, `.brain/tally/<host>.json` keeps per-week counters the hooks would otherwise write as
one event per call: characters injected, injections, writes checked, violations.

## The nightly pass

```
pull       what the other machines pushed (git sync on)                    no LLM
learn      new transcripts: corrections → drafts, turns → events          no LLM
judge*     draft groups → rule | fact | once | noise                      ≤ 3 batches of 20 groups
adopt      fixed rules: lessons, facts, sharpenings, escalations          no LLM
extract*   new sessions → inbox claims                                     ≤ 8 sessions, ≤ 24k chars a call
distill*   inbox claims → notes                                            ≤ 15 claims
curate     retire, demote                                                  no LLM
replay · cache · autotune · index · lint · sync
```

\*LLM steps. They are skipped with `brain nightly --quick` and stop when the day's budget is used up
(`llm_budget_usd` 0.10, `llm_max_calls` 40, booked per call in `.brain/state/llm.json` on each machine;
the cost comes from the `total_cost_usd` Claude Code reports, other CLIs count calls). A lock, taken in one
step, keeps two runs from overlapping. A night the laptop slept through (cron and the Task Scheduler skip it,
launchd skips it when the Mac was off) starts at the next session: the session-start hook sees that the
scheduled run is more than 26 hours old and starts it in the background. `claude` runs headless with no tools,
no settings, no MCP servers, no saved session and without the variables of the session that started it, and every
prompt starts with `[self-improving-brain:internal]`, so these transcripts are never mined. The schedule carries
your `PATH` and XDG folders (cron's own `PATH` lacks `~/.local/bin`, where `claude` usually is); a git call that
hangs (a password prompt, a dead remote) gives up after five minutes.

`distill` replaces outdated text only when it is one line found exactly once, and never inside a lesson, the
frontmatter or code. A new note never takes a name in use. An item the LLM stumbles on gets a second night, then
waits in `brain inbox`; it never blocks the items behind it. A bad answer is about that item; an LLM that is
gone or out of budget stops the step. Text an IDE or Codex puts in front of your message (open files, your
`AGENTS.md`) is not read as something you said, and only a refusal at the start of a tool result counts as one.

## Adoption policy

The judge only sorts. What changes the vault is decided by fixed rules in `learn.adopt`:

- A **rule** becomes a lesson when it came up in **two sessions**, or the user said it **explicitly**
  (*always*, *never*, *from now on*), or it is a strong preference (score ≥ 0.8). Never when it contains a
  link or a secret, never from drafts reported over MCP, never on a topic the user undid, and at most
  `learn_max_new` (5) a night; the rest waits for the next one.
- A **fact** goes to the inbox, where `distill` files it.
- A rule about an **existing lesson** counts only with corrections after the lesson's `since` date. The first
  relapse **sharpens** the rule (the judge's wording replaces it); the next one **escalates** it: to `guard` if
  the lesson has tools or commands and one of the corrections was a rejected tool call, otherwise to `always`
  while there is a free slot.
- A new **guard** needs a rejected tool call and a command regex that stays silent on harmless commands
  (`git status`, `ls -la`, `npm test` …): the brain only asks where you once said no. On a lesson, `tools`
  narrow and `commands`/`paths` decide (`hooks.fires`): `tools: Bash` with a command regex fires on that
  command, not on every shell call. A broad tool alone (Bash, Edit, Read …) never makes a guard.
- One group that fails (a disk error, a note that no longer parses) is logged and skipped; the others go on.
- Drafts that never came back expire after 120 days.

`curate` keeps the set small: a `reflex` or `skill` lesson that has not fired for `retire_after_days` (60) retires,
`always` lessons over `always_max` (8) are demoted to `reflex`, and over `lessons_max` (60) the least recently
fired retire. Guards and `always` lessons never retire for being quiet. A retired lesson keeps its callout with
`retired: <date> · <why>` and is never injected.

## Journal and undo

Every autonomous change is one line in `.brain/journal/<host>.jsonl` (append-only, one file per machine, so
syncing never conflicts on it), with what it takes to undo it:

| kind | change | undo |
|---|---|---|
| `rule` | a new lesson | retired, and its topic is blocked so it is not learned again |
| `sharpen` | a lesson's rule reworded | the old wording |
| `mechanism` | escalated or demoted | the old mechanism |
| `retire` | a quiet lesson, or one over a cap | active again |
| `fact` | appended, updated or a new note | the text leaves the note, the old text comes back, or the new note goes (only if nobody edited it since) |

An undo is itself a journal line (`{"kind": "undo", "target": …}`). `brain review` numbers the changes of the
last 14 days, newest first, and `brain undo <n>` uses those numbers. An undone change keeps its number (shown
dimmed), so a second `undo 1` never hits the next change. Taking back an appended block works only while it
still stands as whole lines; a sentence you added to its line is never glued to the line before.

## Token share

The hooks count every character they inject. The session tokens come from the transcripts: in Claude Code
input + cache creation + output per answer, counted once per message id (cache reads are excluded); in Codex the
last `token_count` event, input minus cached plus output. Share = injected characters / 4 ÷ session tokens,
over the last four weeks. `brain` and the weekly line show it.

## Lesson relapse

A correction after a lesson's `since` date that shares at least two terms with the lesson's rule
and words counts as a relapse. If the lesson was shown in that session, it was **ignored**, so the
rule needs to be sharper or the mechanism harder (`guard`). If it was not shown, it was **missed**,
so the triggers need to be wider.

## Adding an agent

1. **Instructions only:** add a target to `adapters.TARGETS`.
2. **Transcripts:** add a parser in `transcripts.py` that yields `Turn`s and a glob in `config.DEFAULTS["transcripts"]`.
3. **Native hooks:** map the agent's hook payload onto the handlers in `hooks.py`. They only need
   `session_id`, `prompt`, `tool_name`, `tool_input` and `transcript_path`.
4. **MCP:** if the agent speaks MCP, add its name to `mcp.CLIENTS` and its config file to `mcp.client_path`; the server itself is shared.

## Mistake detection

Every user message (and every rejected tool call) gets a confidence score:

| signal | score |
|---|---|
| strong phrase in the opening ("why did you", "I told you", "you should have", "you did it again?!", "warum hast du", "nicht schon wieder") | 0.85 |
| rejected tool call followed by a correction | ≥ 0.75 |
| medium phrase ("that's wrong", "doesn't work", "you forgot", "du solltest", "schon wieder") | 0.55 |
| answer-like opener ("no", "nein", "stop", "no, that…", "yes, but", "ja aber", "actually,") | 0.45, or 0.15 if the agent had just asked a question |
| rejected tool call followed by "sorry, go on" | ignored (accident) |
| lasting preference ("from now on", "always", "never", "ab jetzt") | 0.8; a plain "remember to…" is a to-do (0.35) |

Candidates ≥ 0.4 become drafts and count for the weekly curve until the judge calls them noise. Similar
drafts are grouped (the 500 strongest, so a long history never slows anything down), and groups are ranked
by their best score plus a bonus for every repeat. Transcripts of our own headless LLM calls start with
`[self-improving-brain:internal]` and are never mined. How well this finds corrections in six languages is
measured in [`eval/`](../eval/README.md).

## Backup safety

- `brain init` records whether it created the vault's git repo (`git_owned`). Only then does sync
  stage everything; in a repo the brain joined it stages only `*.md`, `.brain/` and `.gitignore`,
  and sync starts switched off until you turn it on.
- Before every push the remote's visibility is checked (`gh`, or an anonymous `git ls-remote`
  without one): the brain never pushes to a public repo unless `allow_public_remote` is set.
- `gh` always runs against github.com (`GH_HOST`), so a company GHE login never receives your brain.
- "Backed up" means pushed: `brain doctor` compares `HEAD` with its upstream.
- `llm_cmd` and `llm_timeout` describe the machine, not the vault: they live in
  `~/.config/self-improving-brain/config.json` and are never synced to your other laptops.

## Several machines

Everything the brain itself writes is one file per machine: `drafts/`, `inbox/`, `rejected/`, `trust/`,
`journal/`, `events/` and `tally/` under `.brain/`, each with a `<host>.jsonl` or `<host>.json`. A machine
writes only its own file, so two laptops never edit the same lines and the git sync does not stop over them.
Reading merges all files. A draft or an inbox item only moves forward (open → held → approved), so per id the
record whose status got furthest wins; when a machine changes a record another machine wrote (it adopted a
rule from drafts of both), the changed copy goes into its own file. Each machine judges and files only what it
found itself. The name of the machine is fixed on first use (`~/.config/self-improving-brain/host`), because a
laptop's hostname can change with the network. The nightly run pulls before it learns, so a rule adopted on
one laptop is not adopted again on the other. Vaults from 0.1 move their shared files into this machine's
file on first use.

## Repo awareness

At session start the hook resolves the git repo of `cwd` and injects up to three notes that declare
`repo: <name>` in their frontmatter, are tagged with it, or are named after it.

## Trust ledger

A lesson is plain text in a note, so anything that can write a file can plant one. `.brain/trust/<host>.json`
(one file per machine, merged on read: the latest entry per lesson counts) records, for every lesson, the fingerprint (rule, mechanism, triggers, check) it had when it was trusted.

- `trust_mode agent` (default): a lesson you wrote by hand counts once the brain has seen it. A lesson that
  appears or changes while an agent's tool call runs is `pending` and never reaches the agent until
  `brain trust`.
- `trust_mode strict`: only lessons the brain wrote itself (the nightly adoption, `brain lesson`) or lessons
  trusted with `brain trust` count. `trust_mode off` turns the ledger off.
- A change the brain makes to a lesson (sharpen, escalate, retire) keeps its trust only if it had it: a lesson
  that waits for `brain trust` keeps waiting. The judge and the adoption see the same effective set the agent does.
- The commands that change rules, trust or config (`undo`, `trust`, `promote`, `lesson`, `config`, `approve`)
  match `BRAIN_APPROVAL` in `hooks.py`; when an agent runs one, the pre-tool hook asks you first.

## Checks and escalation

A lesson's `check:` is a regex on what the agent writes (`Write`/`Edit` content) or runs (`Bash`). After the
tool call, a match on a `reflex` or `skill` lesson logs a `violation` and tells the agent to fix it; on a
`guard` the pre-tool hook asks before the write happens. Violations are counted per week, and lesson health
flags a lesson broken twice; a correction that comes back escalates it in the nightly run. In headless runs
`BRAIN_GUARD=deny` turns asking into refusing, because nobody can answer.

## Handoff

The PreCompact hook writes `.brain/state/handoff/<session>.json`: your last messages verbatim, the open
question, files touched, the last commands and the repo. After compaction the session-start hook (source
`compact`) injects it as `BRAIN-HANDOFF`; a fresh session in the same repo within 48 h gets a one-line hint to
`brain resume`. A handoff is never an approval: a question that waited for your OK stays open.

## Replay

`brain replay` reads the transcripts of the last N days, finds the corrections (see *Mistake detection*),
and asks for each one: which current lesson is about this mistake (its words match the correction, its
command or check matches one of the last tool calls, or its rule is similar enough), and would it have fired
before the correction, in that turn's prompt or at a tool call. Results: **caught**, **missed** (a lesson exists, its triggers did not fire; replay names the
words that would have) and **uncovered**. Lessons still waiting for `brain trust` are not counted.

## MCP server

`brain mcp serve` speaks newline-delimited JSON-RPC 2.0 over stdio (protocol versions 2024-11-05 to
2025-11-25): `brain_context`, `brain_search`, `brain_read`, `brain_check`, `brain_correction`,
`brain_remember`, `brain_resume`, `brain_handoff`, `brain_stats`. `brain_correction` only creates a draft, and
the nightly run never adopts a draft that came in over MCP; there is no tool to undo, trust or promote.

## brain ui

A `ThreadingHTTPServer` on 127.0.0.1 serves `ui.html` and a JSON API. Every request needs the random
token from the printed link (`?t=` or `X-Brain-Token`), the `Host` header must be `127.0.0.1:<port>` or
`localhost:<port>` (against DNS rebinding), and a POST must come from the page's own origin. The page
ships with a strict CSP and renders vault text as text, never as HTML. `brain ui --demo` serves
synthetic data and needs no vault.
