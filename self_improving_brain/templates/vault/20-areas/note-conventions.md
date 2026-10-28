---
name: note-conventions
description: How to write notes in this vault - one truth per fact, frontmatter fields, hubs, links, lessons.
type: howto
updated: {{TODAY}}
---
# Note conventions

Part of [[hub-brain]].

## One truth
Every fact lives in exactly one note. When something changes, update that line - do not add
a second statement with a newer date somewhere else. `brain lint` flags near-duplicates.

## Frontmatter
```
---
name: kebab-case-name        # stable id, used in [[links]]
description: one line        # recall and INDEX.md use this - make it specific
type: fact | decision | howto | pitfall | preference | project | person | hub
updated: YYYY-MM-DD          # set automatically when the brain edits a note
---
```

## Atomic and linked
One topic per note, a few hundred words. Link it to its hub and to the notes it depends on.
Links make graph recall work: a strong hit passes some of its score to linked notes.

## Sections
Use `##` sections with telling titles. `brain read <note>` shows the outline with token counts,
so agents read one section instead of the whole file.

## Pitfalls
Mark traps with a warning sign at the start of the line, e.g. "Warning: `--bare` needs an API key".
Better still: turn a trap that bit you into a lesson.

## Lessons
A lesson is a callout in the note that owns the topic:
```
> [!lesson]- L-short-name
> rule: one imperative sentence, max ~200 chars
> mechanism: reflex | always | guard | skill
> words: two, or more, keywords
> tools: tool_name_suffix
> commands: regex on the shell command
> paths: regex on file paths
> since: YYYY-MM-DD
```
- `always` - injected at every session start. Keep at most 8.
- `reflex` - injected once per session when a trigger matches (two keywords, a phrase, a tool, a command or a path).
- `guard` - the agent must ask you before the matching tool call runs. Only for irreversible actions.
