# Changelog

## 0.2.0 (unreleased)

### Prove it
- `brain replay` runs your past corrections against today's lessons: caught, missed (with the words that
  would have triggered), uncovered. `--draft` turns the uncovered ones into lesson drafts.
- `brain report` draws a report card; `--public` keeps only numbers, `--badge` makes a README badge.
- Brain-Bench (`bench/run.py`): eight tasks that tempt an agent into a known mistake, run with and
  without the brain, fully isolated. Without `--run` it only prints the plan and a cost ceiling.

### Lessons
- `check:` on a lesson: a regex on what the agent writes or runs. A match is a violation the brain
  counts on its own; on a guard it asks before the write.
- `brain promote <id> --to guard` escalates a rule that keeps getting broken; `brain check` asks
  whether a command would trip a guard.
- `BRAIN_GUARD=deny` refuses instead of asking in headless runs.
- Lesson packs: safety, secrets, testing, git, python, typescript. `brain pack add|remove|export`.
- Team brain: share lessons through a git repo (`brain team add|sync|propose|remove`).
- Trust ledger: a lesson that appears during an agent's tool call stays silent until `brain trust`;
  team and pack lessons count only in the form you accepted.

### Never lose the thread
- PreCompact handoff: your last words verbatim, the open question, the files in flight. They come back
  after compaction; `brain resume` continues in any agent, `brain handoff` writes one from a transcript for agents
  without hooks.

### Knows your work
- `brain skills`: procedures repeated in three or more sessions become skill drafts; approved, they become
  a note, a `skill` lesson and a Claude Code skill.
- `brain atlas`: a codebase map in the repo note; the session start says when it is stale.
- `brain ingest <file|url|folder>` and `--refresh`: sources become notes, and a changed source flags the
  notes built on it.

### Every agent
- MCP server (`brain mcp serve`) and `brain mcp install` for Cursor, Windsurf, Claude Desktop, VS Code,
  Gemini CLI and Codex.
- Claude Code plugin with `/brain-review`, `/brain-distill`, `/brain-ingest`, `/brain-ask`. It steps aside
  when the hooks are already installed in `settings.json`.

### See it
- `brain ui`: progress, lessons, review, replay and the graph in your browser, on 127.0.0.1 with a token.
  `brain ui --demo` shows example data.

### Other
- `brain setup` has eight steps; step 5 installs lesson packs (`--no-packs` skips it).
- The nightly run also mines skills and runs replay.
- The secret scanner knows PyPI and npm tokens and secrets passed as `--password`/`--token` flags.

## 0.1.0

First release: recall, lessons (always, reflex, guard), learning from transcripts, inbox and distill,
`brain stats`, nightly loop with private backup, adapters for Claude Code, Codex, Gemini and `AGENTS.md`.
