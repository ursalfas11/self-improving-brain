---
name: ci-flaky-tests
description: Handling flaky tests in the CI pipeline
type: reference
---
# Handling flaky tests in the CI pipeline

## Policy
A test that fails without a code change is quarantined within a day and gets a ticket. Never rerun the pipeline until it turns green.

## Finding the cause
Look for shared state, clock dependence and test ordering. Run the single test in a loop of fifty to reproduce it.
