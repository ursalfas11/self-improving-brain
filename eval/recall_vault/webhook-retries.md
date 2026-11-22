---
name: webhook-retries
description: How outgoing webhooks are retried
type: reference
---
# How outgoing webhooks are retried

## Policy
A delivery that fails is retried after one minute, ten minutes and one hour. After the third failure the endpoint is marked unhealthy.

## Signature
Every call carries a signature header. Receivers must verify it before trusting the payload.
