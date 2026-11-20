# Contributing

Thanks for helping agents forget less.

## Ground rules
- **Standard library only.** Zero dependencies is a feature: it installs anywhere and audits in an afternoon.
- **Fail open.** Hook code must never block or crash the agent. Catch, log an `error` event, return nothing.
- **Cheap by default.** Anything injected into the agent's context needs a budget and a "once per session" rule; every LLM call counts against the daily budget.
- **A veto, not a duty.** Automation may learn, file, tune and retire on its own, but every change goes into the journal and can be undone. It never turns tool output or an agent's report into a rule, and never deletes a note someone edited.
- **Never real user data in tests or examples.** Use invented transcripts like the ones in `tests/`.

## Dev loop
```bash
python3 -m unittest discover -s tests
BRAIN_VAULT=/tmp/v ./brain init /tmp/v && BRAIN_VAULT=/tmp/v BRAIN_DEBUG=1 ./brain doctor
```
`BRAIN_DEBUG=1` lets hook exceptions surface instead of being swallowed.

Before you push a change to anything the hooks run (the package, `brain`, `hooks/hooks.json`), run the hooks in
real Claude Code sessions too:
```bash
python3 eval/e2e_claude.py          # a throwaway vault, none of your settings; a few cents with haiku
python3 eval/e2e_claude.py --status # 0 if it was green for the code as it is now
```
`--status` fits a pre-push hook: `python3 eval/e2e_claude.py --status || exit 1` in `.git/hooks/pre-push`.

## Most wanted
- native hooks for Codex CLI / Gemini CLI / others (see `docs/architecture.md` → Adding an agent)
- transcript parsers for Cursor, Aider, Grok CLI
- more languages and real-world messages for `eval/` (report the test half, never the dev half)
