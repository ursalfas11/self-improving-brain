---
name: pack-git
description: Git hygiene for agents - no commits straight to main, no skipped hooks, no rewriting pushed history, small named staging.
type: resource
pack: git
pack_version: 1
updated: {{TODAY}}
---
# Pack: git

Lessons for working in a shared repository. Pairs well with the `safety` pack.
Part of [[hub-brain]]. Installed with `brain pack add git`.

> [!lesson]- L-git-no-verify
> rule: Never skip hooks with --no-verify or -n - if a hook fails, fix what it found or ask the user.
> mechanism: guard
> commands: git\s+(commit|push|merge|rebase)\b.*(--no-verify|\s-n\b)
> since: {{TODAY}}

> [!lesson]- L-git-branch-first
> rule: Do not commit or push straight to main or master - create a branch and open a pull request unless the user said otherwise.
> mechanism: guard
> commands: git\s+push\s+(\S+\s+)?(HEAD:)?(main|master)(\s|$)
> since: {{TODAY}}

> [!lesson]- L-git-no-rewrite-pushed
> rule: Do not amend, rebase or squash commits that are already pushed without asking - it rewrites history other people have.
> mechanism: reflex
> commands: git\s+(commit\s+.*--amend|rebase\b)
> since: {{TODAY}}

> [!lesson]- L-git-stage-by-name
> rule: Stage files by name and look at the diff before committing - `git add -A` / `git add .` sweeps in build output, local config and secrets.
> mechanism: reflex
> commands: git\s+add\s+(-A|--all|\.)(\s|$)|git\s+commit\s+.*-a\b
> since: {{TODAY}}
