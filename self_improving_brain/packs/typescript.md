---
name: pack-typescript
description: TypeScript and JavaScript habits - no any or ts-ignore to silence the compiler, no blanket eslint-disable, no stray console.log.
type: resource
pack: typescript
pack_version: 1
updated: {{TODAY}}
---
# Pack: typescript

Lessons for TypeScript/JavaScript code. The checks run on what the agent writes into .ts/.tsx/.js files.
Part of [[hub-brain]]. Installed with `brain pack add typescript`.

> [!lesson]- L-ts-no-any
> rule: Do not silence the type checker with `any` or `as any` - write the real type, or `unknown` plus a narrowing check.
> mechanism: reflex
> paths: \.(ts|tsx|mts|cts)$
> check: :\s*any\b|\bas\s+any\b|<any>
> since: {{TODAY}}

> [!lesson]- L-ts-no-ts-ignore
> rule: Never add @ts-ignore or @ts-nocheck to get a build through - fix the type error, or use @ts-expect-error with a reason the user agreed to.
> mechanism: reflex
> paths: \.(ts|tsx|mts|cts|js|jsx)$
> check: @ts-ignore|@ts-nocheck
> since: {{TODAY}}

> [!lesson]- L-ts-no-blanket-eslint-disable
> rule: Do not disable ESLint for a whole file or without naming the rule - fix the finding or disable one named rule on one line.
> mechanism: reflex
> paths: \.(ts|tsx|js|jsx|mjs|cjs)$
> check: /\*\s*eslint-disable\s*\*/|//\s*eslint-disable(-next-line|-line)?\s*$
> since: {{TODAY}}

> [!lesson]- L-ts-no-console-log
> rule: Remove console.log debugging before you finish - use the project's logger for anything that should stay.
> mechanism: reflex
> paths: (^|/)src/.*\.(ts|tsx|js|jsx)$
> check: \bconsole\.log\(
> since: {{TODAY}}
