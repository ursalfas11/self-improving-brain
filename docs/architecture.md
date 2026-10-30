# Architecture

self-improving-brain is ~7k lines of standard-library Python (3.9+). This page explains the moving parts so you
can change them with confidence.

## Design principles

1. **Rules at the moment beat facts on request.** Most agent mistakes are not missing knowledge
   but known rules that weren't applied. So a lesson is a single imperative sentence injected
   when its trigger matches, not a pointer to a note.
2. **Measure, then tune.** Every injection and every read is logged. Recall weights, the recall
   threshold and lesson health all come from those numbers, not from guesses.
3. **The human approves knowledge, the machine does the bookkeeping.** Drafts, inbox, lint, index,
   sync and tuning run autonomously. Approving a lesson or resolving a contradiction never does.
4. **One truth per fact.** A fact lives in exactly one note. Lessons live inside the note that owns
   their topic. Duplicates are linted.
5. **Cheap by default.** Each prompt gets at most two one-line recalls, each lesson fires once per
   session, and `always` lessons are capped at 8. The CLI returns sections, not files.
6. **Fail open.** A hook that crashes outputs nothing. The agent is never blocked by a broken brain.
   It blocks on purpose only for secrets, writes into `.brain/`, and guards, which ask you rather than refuse.
7. **The agent cannot promote itself.** Anything that turns text into a rule (approve, trust, promote, packs,
   teams, skills) needs a command the agent must ask you for, and a lesson that appeared during a tool call
   stays silent until you trust it.

## Modules

| module | job |
|---|---|
| `notes.py` | parse Markdown: frontmatter subset, H2 sections, `[[links]]`, `[!lesson]` callouts (code blocks ignored) |
| `search.py` | BM25 over sections + learned terms + hit-rate multiplier + graph spreading |
| `events.py` | append-only per-host event log; per-session "already shown" state |
| `inbox.py` | single-claim queue with secret scan and near-duplicate check |
| `transcripts.py` | Claude Code / Codex / generic JSONL → neutral `Turn`s |
| `learn.py` | correction & preference mining, LLM refinement, approve/reject, lesson writing |
| `distill.py` | inbox → wiki: agent work packet or `--auto` via `llm_cmd`, with lint + rollback |
| `metrics.py` | learned recall model, weekly correction curve, lesson health, threshold autotune |
| `hooks.py` | Claude Code hook protocol: session-start (with repo notes and handoff), prompt, pre-tool (guards, `check:` on writes), post-tool (`check:` violations), pre-compact, stop (knowledge check), session-end |
| `cache.py` | parsed vault + BM25 index cached in `~/.cache/self-improving-brain/`, keyed by a fingerprint of every note's size and mtime plus the code version |
| `setup.py` | `brain setup`: vault, agents, PATH, history backfill, lesson packs, optional LLM, private GitHub backup, nightly schedule |
| `adapters.py` | install/uninstall into Claude Code, Codex, Gemini, project `AGENTS.md`; the plugin's hooks and commands |
| `nightly.py` | the autonomous loop and its launchd/cron scheduling, git sync |
| `lint.py` | MUST: secrets, dead links, duplicate lesson ids. SHOULD: orphans, stale projects, near-duplicates, trigger-less lessons |
| `trust.py` | the trust ledger: which lessons count, in which exact form (fingerprint), and which wait for `brain trust` |
| `handoff.py` | PreCompact handoff: your last words verbatim, the open question, files in flight; `brain resume` |
| `replay.py` | past corrections against today's lessons: caught, missed, uncovered, per lesson |
| `report.py` | report card and badge as SVG, numbers only |
| `packs.py` | bundled and external lesson packs: install, edit protection, export with secret scan |
| `team.py` | team brains as git clones under `.brain/team/`, accepted by fingerprint, `propose` on a branch |
| `skills.py` | repeated command sequences across sessions → skill drafts → note, `skill` lesson, Claude Code skill |
| `atlas.py` | codebase map inside the repo note, with the commit it was built at |
| `ingest.py` | files and URLs → source notes; `--refresh` flags notes built on a source that changed |
| `mcp.py` | MCP stdio server (JSON-RPC 2.0) and client config install for Cursor, Windsurf, Claude Desktop, VS Code, Gemini, Codex |
| `ui.py` | `brain ui`: a local page (`ui.html`) and a small JSON API on 127.0.0.1 |

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
correction  {session, ts, week, text, lessons_shown[]}
turns       {session, source, user, corrections, week}
distill     {item, action, note}
violation   {session, id, tool, sample}           a lesson's check: matched what the agent wrote or ran
handoff     {session, repo}
error       {where, msg}
```

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

Candidates ≥ 0.4 become drafts and count for `brain stats`. Similar drafts are grouped (the 500
strongest, so a review backlog never slows a session start), and groups are ranked by their best
score plus a bonus for every repeat. Transcripts of our own
headless LLM calls start with `[self-improving-brain:internal]` and are never mined.

## Knowledge check (Stop hook)

After each answer, the tail of the transcript (≤ 400 KB) is scanned for an unmistakable signal
(score ≥ 0.8: a strong correction or a lasting preference) that was not followed by
`brain remember`/`approve`/`lesson`. Weaker signals never interrupt the agent; they wait for
`brain review`. If there is one, the agent gets a single `decision: block` with the quote and the
exact command to save it. Each moment is checked once (`checked_ts` per session; a resumed session
starts after its old turns), `stop_hook_active` prevents loops, and the hook's own message carries
`[self-improving-brain:check]` so it is never mistaken for a user turn. Turn it off with
`brain config capture_on_stop false`.

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

## Repo awareness

At session start the hook resolves the git repo of `cwd` and injects up to three notes that declare
`repo: <name>` in their frontmatter, are tagged with it, or are named after it.

## Trust ledger

A lesson is plain text in a note, so anything that can write a file can plant one. `.brain/trust.json`
records, for every lesson, the fingerprint (rule, mechanism, triggers, check) it had when it was trusted.

- `trust_mode agent` (default): a lesson you wrote by hand counts once the brain has seen it. A lesson that
  appears or changes while an agent's tool call runs is `pending` and never reaches the agent until
  `brain trust`.
- `trust_mode strict`: only lessons created through the brain (`brain approve`, `brain lesson`, a pack, a team) or
  trusted with `brain trust` count. `trust_mode off` turns the ledger off.
- Team and external pack lessons count only in the exact form you accepted. A later change waits for
  `brain team sync`.
- The approval commands match `BRAIN_APPROVAL` in `hooks.py`; when an agent runs one, the pre-tool hook
  asks you first.

## Checks and escalation

A lesson's `check:` is a regex on what the agent writes (`Write`/`Edit` content) or runs (`Bash`). After the
tool call, a match on a `reflex` or `skill` lesson logs a `violation` and tells the agent to fix it; on a
`guard` the pre-tool hook asks before the write happens. `brain stats` counts violations per week, and a
lesson broken twice is suggested for `brain promote <id> --to guard`. In headless runs `BRAIN_GUARD=deny`
turns asking into refusing, because nobody can answer.

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
`brain_remember`, `brain_resume`, `brain_handoff`, `brain_stats`. `brain_correction` only creates a draft;
there is no tool to approve, trust or promote.

## brain ui

A `ThreadingHTTPServer` on 127.0.0.1 serves `ui.html` and a JSON API. Every request needs the random
token from the printed link (`?t=` or `X-Brain-Token`), the `Host` header must be `127.0.0.1:<port>` or
`localhost:<port>` (against DNS rebinding), and a POST must come from the page's own origin. The page
ships with a strict CSP and renders vault text as text, never as HTML. `brain ui --demo` serves
synthetic data and needs no vault.
