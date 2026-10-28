---
name: working-style
description: How I want agents to work with me - answer style, verification, what needs my OK. Lessons without a better home land here.
type: preference
updated: {{TODAY}}
---
# Working style

Rules for every agent, learned from real sessions. Linked from [[hub-brain]].
Edit freely: these three are starter defaults, delete what does not fit you.

> [!lesson]- L-result-first
> rule: Lead with the result in a few lines; details only when asked - but never leave out a part of what was asked.
> mechanism: always
> since: {{TODAY}}

> [!lesson]- L-verify-before-done
> rule: Before saying something works or is done, run it or test it and show the output; if you could not verify, say so plainly.
> mechanism: always
> since: {{TODAY}}

> [!lesson]- L-ask-before-force-push
> rule: Force-pushing rewrites shared history - show what will be overwritten and get an explicit OK first.
> mechanism: guard
> commands: git push.*(--force|-f\b)
> since: {{TODAY}}
