# Security

self-improving-brain makes no network calls of its own. Data leaves your machine only through the `llm_cmd`
you configure and the git remote you set up.

Secrets are blocked at three points: `brain remember`, agent Write/Edit into the vault (Claude Code
`PreToolUse` deny), and `git commit` (pre-commit hook installed by `brain init`). The patterns live in
`self_improving_brain/secrets.py`. If you find a secret format that slips through, please open an issue
**without** a real secret in it.

A rule that an agent writes into your notes reaches the agent only once a message you typed in that session
backs it; otherwise the nightly run takes it back. The evidence is read from the session transcript, so the
hooks refuse shell commands that write into `.brain/` or into a transcript. That check reads the command text
and stops the plain cases. A process on your machine that hides where it writes is outside what a hook can
see: if you find a way to get a rule trusted without the user's words, please report it.
