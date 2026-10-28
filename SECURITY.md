# Security

self-improving-brain makes no network calls of its own. Data leaves your machine only through the `llm_cmd`
you configure and the git remote you set up.

Secrets are blocked at three points: `brain remember`, agent Write/Edit into the vault (Claude Code
`PreToolUse` deny), and `git commit` (pre-commit hook installed by `brain init`). The patterns live in
`self_improving_brain/secrets.py`. If you find a secret format that slips through, please open an issue
**without** a real secret in it.
