---
name: dependency-updates
description: Updating third party dependencies safely
type: reference
---
# Updating third party dependencies safely

## Routine
A bot opens update pull requests on Mondays. Patch versions merge when the tests pass. Minor versions need a glance at the release notes.

## Major versions
A major upgrade gets its own ticket and a branch. Pin the version until the migration is done.
