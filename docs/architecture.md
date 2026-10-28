# Architecture

self-improving-brain is ~4k lines of standard-library Python (3.9+). This page explains the moving parts so you
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
6. **Fail open.** A hook that crashes outputs nothing. The agent is never blocked by a broken brain,
   and the only blocking it does on purpose is the secret block.

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
| `hooks.py` | Claude Code hook protocol: session-start (with repo notes), prompt, pre-tool, post-tool, stop (knowledge check), session-end |
| `cache.py` | parsed vault + BM25 index cached in `~/.cache/self-improving-brain/`, keyed by a fingerprint of every note's size and mtime plus the code version |
| `setup.py` | `brain setup`: vault, agents, PATH, history backfill, optional LLM, private GitHub backup, nightly schedule |
| `adapters.py` | install/uninstall into Claude Code, Codex, Gemini, project `AGENTS.md` |
| `nightly.py` | the autonomous loop and its launchd/cron scheduling, git sync |
| `lint.py` | MUST: secrets, dead links, duplicate lesson ids. SHOULD: orphans, stale projects, near-duplicates, trigger-less lessons |

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
3. **Native hooks:** map the agent's hook payload onto the five handlers in `hooks.py`. They only need
   `session_id`, `prompt`, `tool_name`, `tool_input` and `transcript_path`.

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

