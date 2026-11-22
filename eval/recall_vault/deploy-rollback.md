---
name: deploy-rollback
description: How to roll back a failed deployment on the staging cluster
type: reference
---
# How to roll back a failed deployment on the staging cluster

## Decide
Roll back when the health check fails twice or the error budget is gone. Do not try to fix forward on a Friday.

## Steps
Run the rollback job in the pipeline with the previous release tag. The job redeploys the old image and waits for the readiness probe. Announce it in the engineering channel.
