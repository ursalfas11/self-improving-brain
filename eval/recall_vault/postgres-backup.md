---
name: postgres-backup
description: Nightly database backups and the quarterly restore drill
type: reference
---
# Nightly database backups and the quarterly restore drill

## Backups
A snapshot of the main database runs every night at 02:00 and is kept for 35 days. Snapshots land in the cold storage bucket.

## Restore drill
Every quarter one engineer restores the latest snapshot into a scratch instance and compares row counts. A backup that was never restored does not count.
