---
name: pack-safety
description: Guards for irreversible actions - deleting files, resetting git, dropping data, destroying infrastructure, publishing. The agent asks you first.
type: resource
pack: safety
pack_version: 1
updated: {{TODAY}}
---
# Pack: safety

Guard lessons for actions you cannot take back. A guard makes the agent stop and ask you before the command runs.
Part of [[hub-brain]]. Installed with `brain pack add safety`; edit or delete freely, `brain pack remove safety` takes it out again.

> [!lesson]- L-safety-rm-rf
> rule: Recursive deletes cannot be undone - list what would be removed and get an explicit OK before running them.
> mechanism: guard
> commands: \brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR]|--recursive)|\bfind\b.*\s-delete\b
> since: {{TODAY}}

> [!lesson]- L-safety-git-discard
> rule: git reset --hard, git clean -f and git checkout/restore of a whole tree throw away uncommitted work - show `git status` and ask first.
> mechanism: guard
> commands: git\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+(--\s+)?\.(\s|$)|restore\s+(--\S+\s+)*\.(\s|$)|stash\s+(drop|clear))
> since: {{TODAY}}

> [!lesson]- L-safety-drop-data
> rule: Dropping or truncating tables, deleting rows without WHERE, flushing caches or databases destroys data - name the target and get an OK first.
> mechanism: guard
> commands: (?i)\b(drop\s+(table|database|schema|collection)|truncate\s+(table\s+)?\w+|delete\s+from\s+\w+\s*(;|"|'|$)|flushall|flushdb|dropDatabase\(\))
> since: {{TODAY}}

> [!lesson]- L-safety-destroy-infra
> rule: Destroying or deleting cloud and cluster resources is irreversible - show the plan or the exact resources and ask before running it.
> mechanism: guard
> commands: \b(terraform\s+(destroy|apply\s+.*-auto-approve)|pulumi\s+destroy|kubectl\s+delete|helm\s+(uninstall|delete)|aws\s+\S+\s+(delete|terminate|remove)-\S+|gcloud\s+.*\bdelete\b|az\s+.*\bdelete\b|docker\s+(system|volume)\s+prune)
> since: {{TODAY}}

> [!lesson]- L-safety-publish
> rule: Publishing a package, pushing a release tag or deploying to production is outward-facing - confirm version and target with the user first.
> mechanism: guard
> commands: \b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+release\s+create\b|\bgit\s+push\b.*--tags|\b(vercel|netlify)\b.*--prod|\bfly\s+deploy\b
> since: {{TODAY}}

> [!lesson]- L-safety-chmod-sudo
> rule: Do not run sudo or open permissions wide (chmod -R 777, chown -R on system paths) without asking - fix the actual permission problem instead.
> mechanism: guard
> commands: ^\s*sudo\s|\bchmod\s+(-R\s+)?0?777\b|\bchown\s+-R\s+\S+\s+/(usr|etc|var|opt|Library)\b
> since: {{TODAY}}
