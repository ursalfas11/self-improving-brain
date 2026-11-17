# Changelog

## 0.3.1

A review for production: what a secret, a planted file or a second laptop could still do, and where the nightly
loop still stopped and waited for you.

### Secrets
- The scan now finds a secret name inside a longer one (`GITHUB_TOKEN=`, `DB_PASSWORD=`,
  `aws_secret_access_key =`), quoted JSON keys (`"password": "…"`), `Bearer` tokens and SendGrid keys. References
  such as `os.environ[…]`, `getenv(…)`, `${{ secrets.X }}` and plain numbers are still not findings.
- Text from a session is redacted when it is read, so a token you pasted never reaches the LLM CLI, the event
  log or a draft. The prompt to the LLM CLI is redacted once more before it leaves.
- `brain:allow-secret` is your word, so only your own paths honour it: the pre-commit scan and lint of your notes.
  An agent's write into the vault, the inbox and every check of model output ignore it.

### Planted files
- An agent's Write, Edit or shell redirect into the per-machine config (`llm_cmd` is run by the nightly job) or
  into the vault's `.git/` (git hooks) is refused.
- `brain undo` never follows a journal entry that points outside the vault or into `.git/`, whatever its
  checksum says. Journals arrive from every machine through git.
- Only `github.com` itself counts as a GitHub remote for the "never push to a public repo" check.
- MCP tools pass what an agent sends as values, never as options (`--help` as a search query stays a query).
- The Windows task is created without `cmd.exe`; CI runs with a read-only token.

### Nothing stops for good
- A pull that conflicts (two laptops added a line to the same note) is retried once with git's union merge
  for notes and logs: both lines stay, and the backup goes on instead of waiting for you.
- `brain setup` without an LLM CLI remembers that you wanted learning; the first night after you install one
  switches it on. `--no-llm` stays off.
- A night that broke (LLM login, network, backup) is tried once more at your next session after six hours, not
  only the next night. A lock left by a crashed run no longer blocks that catch-up.

### Smaller
- Five functions and constants nothing called are gone.

## 0.3.0

Nothing left to approve. A rule an agent wrote used to stay silent until you ran `brain trust`. Now your own
words decide, and the brain settles it without you.

### Evidence instead of approval
- A lesson that appears or changes during an agent's tool call counts once a message **you typed** in that
  session carries it: at once when the wording is yours, at night when a model finds it and quotes you word for
  word. Tool results, pasted text, hook and agent text and messages from another session never count.
- Without evidence the nightly run takes the lesson back after the session has been quiet for two hours: a new
  one leaves the note, a changed one returns to the form that counted before. Both are in `brain review`;
  `brain undo` reverses either.
- An answer you picked in a question dialog counts like a message you typed, except for `always` and `guard`.
  If only a lesson's trigger changed, naming the lesson and the trigger is enough.
- `always` and `guard` need you to have said it as a rule. A rule with a secret, or with an address or download
  command you did not name, never counts on its own.
- The pre-tool hook no longer asks about brain commands. `brain lesson`, `promote`, `undo` and `approve` run
  freely, and inside an agent session what they write needs the same evidence. `brain trust` and changes to
  `trust_mode`, `allow_public_remote`, `guard_brain_commands`, `llm_cmd` and `transcripts` are refused for an
  agent unless you named them in the session.
- Shell commands that write into `.brain/` or into a session transcript are refused, and so is an agent's
  Write or Edit of a transcript. Inside an agent session `brain learn --file` only reads logs below a
  `transcripts` folder.
- A correction an agent reported over MCP stays the agent's wording: approved inside an agent session it
  needs your own words like any lesson an agent wrote. From your own terminal `brain approve` vouches for it.
- The nightly summary says what was confirmed by your words and what was taken back.
- Coming from 0.2: a lesson that still waits for `brain trust` is settled by the next nightly run like any
  other. With evidence it counts, without it is taken back, and `brain undo` brings it back. To keep one for
  sure, run `brain trust <id>` before you update.

## 0.2.0

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
- **Never behind GitHub.** Every night the brain asks GitHub (one batched call through your `gh` login, cached)
  for the state of the pull requests and issues your notes mention. A line that still calls a merged or closed
  one open becomes a correction in the inbox, is fixed by the same writer as every fact and shows up in
  `brain review`. `brain lint` names such lines from the cache, without a call. Only owner, repository and number
  leave your machine; `brain config github_check false` turns it off.
- **A contradiction is settled, not asked.** The judge names the existing rule a correction would contradict.
  What you said before the rule existed is what the rule replaced, and is closed. What you said plainly after it
  (*from now on*, *remember*) replaces the rule: the old one retires, and `brain undo` brings it back. Anything
  said in passing waits and expires like every correction that never came back.
- **Tidies up.** What `brain lint` lists as upkeep is fixed at night (or now, with `brain care`): a note without
  a description gets its first sentence, a note that links to nothing is linked to the hub it fits, a reflex rule
  without a trigger gets the rule's own words, a rule longer than 240 characters is shortened, and two notes that
  look alike are read by the LLM and merged when they say the same, or remembered as different. Every fix is
  checked (no number, link, code or rule lost, no secret, no new MUST finding), journaled, and not repeated once
  you undid it. A note with uncommitted changes is left alone. `care_max` (3) caps the fixes that need the LLM.
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
  code; a new note never takes a name you use. No item waits for you: a claim that could not be filed is tried
  again the next night with the reason in the prompt and dropped after three nights (`brain undo` puts it back);
  an answer the LLM garbled only moves the item to the back of the queue, so a broken CLI can not empty the
  inbox. One broken group no longer stops the night. The schedule finds `claude` (cron's `PATH` lacked
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
