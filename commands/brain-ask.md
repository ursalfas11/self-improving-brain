---
description: Answer a question from the brain with citations, and offer to save a good answer as a note
argument-hint: <question>
---
`brain` below is the brain command shown in the SELF-IMPROVING-BRAIN line of your context.

Question: $ARGUMENTS

1. `brain search "<question>"`, then `brain read <note> <section>` for the best hits. Follow links with `brain neighbors <note>` if needed.
2. Answer from the notes only and cite them as [[note]]. Mark anything not backed by a note as an assumption. Mention the `updated` date when the answer depends on freshness.
3. If the answer took real synthesis across several notes, offer to save it: `brain remember fact "<the answer in one sentence>"` or a new note linked from the hub.
