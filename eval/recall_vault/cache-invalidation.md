---
name: cache-invalidation
description: Cache invalidation for the product catalogue
type: reference
---
# Cache invalidation for the product catalogue

## Rule
Catalogue entries are cached for ten minutes. A price change purges the entry by key, never the whole cache.

## Debugging
If a customer sees an old price, check the cache key first and the purge queue second.
