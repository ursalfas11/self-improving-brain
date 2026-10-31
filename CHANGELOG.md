# Changelog

## 0.2.0 (unreleased)

Install once, then forget it. The brain now learns on its own every night, and you keep a veto instead
of a review queue.

### Learns on its own
- Every night a small model reads the day's sessions through the LLM CLI you already have (Haiku with your
  `claude` login by default, or codex/ollama; no API key is stored). It sorts each correction: a lasting rule,
  a fact, a one-off, or noise.
- Fixed rules decide what changes. A rule needs **two sessions**, or your explicit *always*/*never*/*from now on*.
  Rules come only from your own words, never from tool output, an agent's MCP report, or text with a link or
  a secret. At most five new rules a night.
- A rule that comes back although it exists is **sharpened**, then **escalated**: to `always`, or to a `guard`
  where you had stopped a tool call. A guard needs a command regex that stays silent on everyday commands.
- **Knowledge, too.** Decisions, facts, pitfalls and preferences from your sessions (your words and the agent's
  answers, never tool output) are filed into the note that owns them; a newer statement replaces the outdated
  one.
- **Cleans up.** A rule that has not fired for 60 days retires; at most eight `always` rules and 60 rules in all.
- **Hard budget:** `llm_budget_usd` 0.10 and `llm_max_calls` 40 a day, booked per call. When it is used up, the
  rest waits for the next night.
- **No night is lost.** A night the laptop slept through (cron and the Task Scheduler skip it) runs at the next
  session, in the background. The lock is taken in one step, so two runs never overlap; on Windows it no longer
  checks the other run with `os.kill`, which would have ended it.
- **Several laptops, one brain.** Drafts, inbox and trust ledger are one file per machine, like the journal, and
  the nightly run pulls before it learns: two laptops no longer stop each other's sync, and a rule is learned
  once. The machine name is fixed on first use. Vaults from 0.1 are moved over on first use.
- **Safe while you sleep.** A fact replaces one line found exactly once, never inside a rule, the frontmatter or
  code; a new note never takes a name you use. An item the LLM stumbles on waits for you without blocking the
  rest, and one broken group no longer stops the night. The schedule finds `claude` (cron's `PATH` lacked
  `~/.local/bin`), and a hanging git gives up after five minutes.

### A veto, not a duty
- Every change the brain makes on its own goes into `.brain/journal/<host>.jsonl`. `brain review` lists the
  last two weeks, `brain undo <n>` takes one back and says what came back, and an undone topic is never learned
  again, also when it returns in other words ("retries" for "retry", "flaky tests" for "flaky test").
- Once a week the first session shows one line: what was learned, the trend, what the brain costs you.
- `brain ui`: Overview, Learned (with undo), Rules, Replay, Map.
- An undone change keeps its number, so a second `brain undo 1` never hits the next one.

### Measures itself
- `brain` shows mistakes per 100 messages against the weeks before you installed it, once two full weeks with the
  brain are over; until then it shows only your baseline. Corrections the judge called noise leave the curve.
- The brain's share of your agent's tokens: what the hooks injected against the session tokens in your
  transcripts (Claude Code and Codex).
- What an IDE or Codex puts in front of your message (open files, your `AGENTS.md`) is not read as yours, and a
  failing test that mentions a denial is no refusal. Two notes with one name both stay reachable; lint names them.
- `eval/`: 250 hand-labelled replies in six languages. The correction detector learned Spanish, French,
  Portuguese and Italian. On the held-out half it now flags with 93 % precision and finds 86 % of the
  lasting corrections, up from 12 %.

### Quiet and small
- `brain setup` asks nothing: vault, agents, the `brain` command, your past sessions as a baseline, learning,
  the nightly run. `--github` adds a private backup. The installer runs it without a terminal too, so an agent can
  install the brain for you; a second `brain setup` knows your history and starts learning once an LLM CLI is there.
- **Nothing fails quietly.** A failed login, a broken step or a failed backup shows in `brain`, in `brain doctor`
  and once at the next session start; a used-up budget shows in the last run's line. A machine without a git name
  commits as `brain`.
- `brain` alone shows how it is doing. Eight visible commands; the ones agents use are in `brain help all`.
- `brain uninstall` takes it out of every agent and the schedule, the secret scan out of your notes' git hook, and
  removes the `brain` link, its cache and settings, files that held only the brain's part, and its backups; it names
  the last step for the program itself. Your notes stay. A secret scan whose program is gone steps aside instead of
  blocking your commits.
- The session start is a few lines: where the memory is and your `always` rules. The Stop hook is gone. Claude
  Code gets no block in `CLAUDE.md` any more (the hooks say it where it matters): about 450 tokens less in every
  session. The block for Codex and Gemini names the long command once, and counts in the token share.
- Recall skips a note the session has seen instead of showing nothing, and no longer points at a note that holds
  only rules. New rule ids are three words (`L-fix-flaky-tests`). `brain --version`; an unknown command is one line.
- One `/brain` command for Claude Code replaces `/brain-review`, `/brain-distill`, `/brain-ingest` and `/brain-ask`.

### Prove it
- `brain replay` runs your past corrections against today's rules: caught, missed (with the words that
  would have triggered), uncovered. Corrections a rule was learned from match it by design and are counted apart.
- `brain report` draws a report card; `--public` keeps only numbers, `--badge` makes a README badge.
- Brain-Bench (`bench/run.py`): eight tasks that tempt an agent into a known mistake, run with and
  without the brain, fully isolated. Without `--run` it only prints the plan and a cost ceiling.

### Rules
- `check:` on a rule: a regex on what the agent writes or runs. A match is a violation the brain counts on its
  own; on a guard it asks before the write.
- `BRAIN_GUARD=deny` refuses instead of asking in headless runs.
- A guard's tools narrow and its command decides: a guard learned on the shell asks on its command, not on every
  `git status`. Guards read every line of a command, but not a heredoc's body.
- Trust ledger: a rule that appears during an agent's tool call stays silent until you trust it, and a change the
  brain makes to it does not trust it either. Undo, trust and config are commands an agent has to ask you for.

### Never lose the thread
- PreCompact handoff: your last words verbatim, the open question, the files in flight. They come back
  after compaction; `brain resume` continues in any agent, `brain handoff` writes one from a transcript for agents
  without hooks.

### Every agent
- MCP server (`brain mcp serve`). `brain setup` connects Cursor, Windsurf, Claude Desktop and VS Code;
  `brain mcp install` also Gemini CLI and Codex.

### Removed
- Lesson packs, the team brain, `brain skills`, `brain atlas` and `brain ingest`. Each did one thing well; together
  they made the brain wider than it should be. Your own sessions replace the packs.
- The review queue: `approve` and `reject` remain as hidden commands for doing it by hand.

## 0.1.0

First release: recall, lessons (always, reflex, guard), learning from transcripts, inbox and distill,
`brain stats`, nightly loop with private backup, adapters for Claude Code, Codex, Gemini and `AGENTS.md`.
