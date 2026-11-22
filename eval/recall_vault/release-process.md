---
name: release-process
description: Tagging, changelog and publishing a release
type: reference
---
# Tagging, changelog and publishing a release

## Before
Merge only green builds. Move the entries under Unreleased in the changelog to the new version heading.

## Publish
Tag the commit with the version, push the tag and let the pipeline build the artifacts. Do not publish from a laptop.
