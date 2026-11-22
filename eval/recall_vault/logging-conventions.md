---
name: logging-conventions
description: Structured logging: levels, fields and what never to log
type: reference
---
# Structured logging: levels, fields and what never to log

## Levels
Use error for things a human must look at, warn for recoverable surprises, info for business events. Debug stays off in production.

## Fields
Every line carries the request id and the customer id. Never log passwords, tokens or full card numbers.
