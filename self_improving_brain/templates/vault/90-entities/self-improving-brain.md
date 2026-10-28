---
name: self-improving-brain
description: The tool behind this vault - CLI commands, hooks and the nightly loop at a glance.
type: fact
updated: {{TODAY}}
---
# self-improving-brain

Part of [[hub-brain]]. Conventions: [[note-conventions]].

## Commands agents use
- `brain search "<question>"` - best sections, with dates.
- `brain read <note> [section]` - outline with token counts, or one section.
- `brain remember <type> "<sentence>"` - into the inbox, never straight into a note.
- `brain review` / `brain approve` - lesson drafts from your corrections.
- `brain stats` - is it getting smarter? Corrections per 100 messages, recall precision, lesson health.

## The nightly loop
`brain schedule install` runs `brain nightly` on this machine: learn from transcripts, refresh
the recall model, tune the recall threshold, flag weak lessons, rebuild INDEX.md, lint, git sync.
It never approves lessons or deletes knowledge on its own.
