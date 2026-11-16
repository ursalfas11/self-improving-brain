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
- `brain check --command "<cmd>"` - would this trip a guard?

## Commands for you
- `brain` - how it is doing: mistakes since the start, what it learned this week, what it costs.
- `brain review` / `brain undo <n>` - what it changed on its own lately, and taking one change back.

## The nightly loop
Every night `brain nightly` reads the day's sessions: corrections you gave twice (or with *always* or
*never*) become rules, decisions and facts are filed into their notes, quiet rules retire. Then it
refreshes the recall model, rebuilds INDEX.md, lints and syncs. Every change it makes is in the journal
and can be undone; it never turns tool output into a rule and never deletes a note you edited.
