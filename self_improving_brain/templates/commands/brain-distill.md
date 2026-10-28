---
description: Work the brain's inbox into the wiki - one truth per fact, linked to its hub
---
Run `{{BRAIN}} distill`. It lists every inbox item with the notes it most likely belongs to.

For each item:
- Put the claim into the ONE note that owns the topic. If that note already states something about it, update that statement instead of adding a second one.
- No fitting note? Create one in the right folder with frontmatter (`name`, `description`, `type`, `updated`) and link it from its hub.
- The new text contradicts what is there? Do not resolve it yourself - ask the user.
- Then `{{BRAIN}} inbox done <id>`.

Finish with `{{BRAIN}} lint` and `{{BRAIN}} index`. Summarize what changed in a few lines.
