---
name: pack-secrets
description: Keep credentials out of code, logs, commits and the chat - no hard-coded keys, no printing env files, no committing .env.
type: resource
pack: secrets
pack_version: 1
updated: {{TODAY}}
---
# Pack: secrets

Lessons that keep credentials where they belong. The `check:` rules spot a key the moment the agent writes it.
Part of [[hub-brain]]. Installed with `brain pack add secrets`.

> [!lesson]- L-secrets-no-hardcoded-keys
> rule: Never write API keys, tokens or private keys into source files - read them from the environment or the project's secret store.
> mechanism: guard
> check: AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{36}\b|\bsk-[A-Za-z0-9_-]{20,}|\bxox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}
> since: {{TODAY}}

> [!lesson]- L-secrets-no-print-env
> rule: Do not print secret files or the whole environment into the session (cat .env, printenv, env) - check for a variable's presence instead, e.g. `test -n "$NAME"`.
> mechanism: guard
> commands: \b(cat|less|more|head|tail|bat)\s+\S*\.env(\.(local|dev|development|prod|production|staging|test))?(\s|$|["'|;&])|^\s*(printenv|env)\s*$|\bcat\s+\S*(id_rsa|id_ed25519|\.pem|credentials)\b
> since: {{TODAY}}

> [!lesson]- L-secrets-no-commit-env
> rule: Never stage .env files, key files or credentials - add them to .gitignore and commit an .env.example without values.
> mechanism: guard
> commands: git\s+add\s+.*(\.env(\s|$|\.(?!example))|\.pem\b|id_rsa|credentials\.json|\.p12\b)
> since: {{TODAY}}

> [!lesson]- L-secrets-ask-dont-guess
> rule: When a credential is missing, name the variable and where it goes and let the user set it - never ask them to paste the secret into the chat.
> mechanism: reflex
> words: api key, token, password, secret, credentials, env var
> since: {{TODAY}}
