---
description: Review lesson drafts mined from past sessions and turn the real mistakes into lessons (with the user's OK)
---
`brain` below is the brain command shown in the SELF-IMPROVING-BRAIN line of your context.

Run `brain learn` and then `brain review`.

For each draft:
1. Decide whether it reveals a reusable rule (a mistake likely to repeat, or a lasting preference). One-off task details and plain follow-up questions are noise.
2. For real ones, write ONE imperative rule (max ~200 chars) that would have prevented it, pick the mechanism (`reflex` by default, `always` only if it applies to every session, `guard` only for irreversible actions) and 2-4 trigger words. Find the note that owns the topic with `brain search`.
3. Show the user a compact table: draft id, what happened, proposed rule, mechanism, target note. Wait for an explicit OK per row.
4. Then run `brain approve <id> --rule "..." --mechanism ... --words "..." --note <note>` for the approved ones and `brain reject <id>` for noise (`--block word word` if the user never wants that topic).

Never approve without the user's OK.
