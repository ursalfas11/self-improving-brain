---
name: rate-limiting
description: API rate limits per customer and how to raise them
type: reference
---
# API rate limits per customer and how to raise them

## Limits
The public API allows 600 requests per minute per customer. Bursts up to 100 are tolerated. Beyond that clients get status 429 with a retry-after header.

## Raising a limit
Sales files a request with the expected traffic. Platform raises the limit for one month and reviews the usage afterwards.
