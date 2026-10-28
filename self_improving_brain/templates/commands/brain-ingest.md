---
description: Work a source (file, URL, repo, PR or this session) into the brain's wiki
argument-hint: <file | url | repo | "this session">
---
Source: $ARGUMENTS

1. Read the source. If it is large (many files, long docs), delegate the reading to a subagent and ask it for the durable facts, decisions and pitfalls only.
2. For each durable item, find its home with `{{BRAIN}} search "<topic>"`. Prefer updating existing notes over creating new ones; keep one truth per fact.
3. Propose the changes to the user as a short list (note -> what changes). Write only after the OK.
4. Link every new note to its hub and related notes, then run `{{BRAIN}} lint` and `{{BRAIN}} index`.
