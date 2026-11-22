---
name: secrets-management
description: Where secrets live and why they never go into git
type: reference
---
# Where secrets live and why they never go into git

## Rule
Passwords, tokens and certificates live in the secrets manager. A secret that reached git counts as leaked and is rotated.

## Local work
Fetch secrets at start with the helper command. Never paste them into a chat or a ticket.
