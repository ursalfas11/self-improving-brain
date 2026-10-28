# Contributing

Thanks for helping agents forget less.

## Ground rules
- **Standard library only.** Zero dependencies is a feature: it installs anywhere and audits in an afternoon.
- **Fail open.** Hook code must never block or crash the agent. Catch, log an `error` event, return nothing.
- **Cheap by default.** Anything injected into the agent's context needs a budget and a "once per session" rule.
- **Human approves knowledge.** Automation may draft, queue, lint and tune. It may not approve lessons or delete notes.
- **Never real user data in tests or examples.** Use invented transcripts like the ones in `tests/`.

## Dev loop
```bash
python3 -m unittest discover -s tests
BRAIN_VAULT=/tmp/v ./brain init /tmp/v && BRAIN_VAULT=/tmp/v BRAIN_DEBUG=1 ./brain doctor
```
`BRAIN_DEBUG=1` lets hook exceptions surface instead of being swallowed.

## Most wanted
- native hooks for Codex CLI / Gemini CLI / others (see `docs/architecture.md` → Adding an agent)
- transcript parsers for Cursor, Aider, Grok CLI
- a labelled eval set + `brain eval` to benchmark recall and lesson precision
