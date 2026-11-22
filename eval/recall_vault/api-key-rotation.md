---
name: api-key-rotation
description: Rotating the payment provider API keys every 90 days
type: reference
---
# Rotating the payment provider API keys every 90 days

## Schedule
Keys for the payment provider expire after 90 days. Rotation happens on the first Monday of the quarter. Create the new key in the provider console before you revoke the old one.

## Rollout
Store the new key in the secrets manager, restart the checkout service, then watch the error rate for ten minutes. Revoke the old key only after the error rate stays flat.
