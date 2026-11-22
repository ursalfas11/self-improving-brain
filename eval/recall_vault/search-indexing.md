---
name: search-indexing
description: Rebuilding the search index after schema changes
type: reference
---
# Rebuilding the search index after schema changes

## When
Reindex after a mapping change or when search results lag behind the database by more than an hour.

## How
Create a new index, fill it in the background, switch the alias, delete the old index a day later.
