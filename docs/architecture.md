# Architecture

self-improving-brain is ~2k lines of standard-library Python. This page explains the moving parts so you
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
| `hooks.py` | Claude Code hook protocol: session-start, prompt, pre-tool, post-tool, session-end |
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
