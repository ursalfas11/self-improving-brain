---
description: Answer a question from the brain with citations, and offer to save a good answer as a note
argument-hint: <question>
---
Question: $ARGUMENTS

1. `{{BRAIN}} search "<question>"`, then `{{BRAIN}} read <note> <section>` for the best hits. Follow links with `{{BRAIN}} neighbors <note>` if needed.
2. Answer from the notes only and cite them as [[note]]. Mark anything not backed by a note as an assumption. Mention the `updated` date when the answer depends on freshness.
3. If the answer took real synthesis across several notes, offer to save it: `{{BRAIN}} remember fact "<the answer in one sentence>"` or a new note linked from the hub.
