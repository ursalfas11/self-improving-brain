---
name: pack-testing
description: Honest tests - fix flaky tests at the root, never skip or weaken a failing test to get green, run what you touched before saying done.
type: resource
pack: testing
pack_version: 2
updated: {{TODAY}}
---
# Pack: testing

Lessons against the classic ways agents make CI green without making the code right.
Part of [[hub-brain]]. Installed with `brain pack add testing`.

> [!lesson]- L-testing-no-retry-flaky
> rule: Fix flaky tests at the root (timing, shared state, order); never paper over them with retries or a flaky marker.
> mechanism: reflex
> words: flaky, intermittent, retry, is flaky, flaky test, tests are flaky, sometimes fails, randomly fails, fails sometimes
> paths: (^|/)(tests?|spec|__tests__)/|_test\.|\.test\.|\.spec\.|test_
> check: @retry\b|pytest\.mark\.flaky|@flaky\b|jest\.retryTimes|retries:\s*[1-9]|\.retry\(\d
> since: {{TODAY}}

> [!lesson]- L-testing-no-skip-to-green
> rule: Never skip, delete or comment out a failing test to get CI green - find out why it fails, or ask the user before disabling it.
> mechanism: reflex
> words: failing test, ci is red, ci red, get it green, make ci green, ci green, tests fail, broken test, build is red
> paths: (^|/)(tests?|spec|__tests__)/|_test\.|\.test\.|\.spec\.|test_
> check: @pytest\.mark\.skip|@unittest\.skip|\b(it|test|describe)\.skip\(|\bx(it|describe|test)\(|\bt\.Skip\(|@Disabled\b|@Ignore\b
> since: {{TODAY}}

> [!lesson]- L-testing-no-focused-tests
> rule: Do not leave focused tests (.only, fit, fdescribe) behind - they silently turn off the rest of the suite.
> mechanism: reflex
> paths: (^|/)(tests?|spec|__tests__)/|\.test\.|\.spec\.
> check: \b(it|test|describe)\.only\(|\bf(it|describe)\(
> since: {{TODAY}}

> [!lesson]- L-testing-dont-weaken-asserts
> rule: When a test fails, do not loosen the assertion or update the snapshot until you know the new behaviour is right - say which one changed and why.
> mechanism: reflex
> words: assertion, snapshot, expected, update snapshot, test fails
> commands: (jest|vitest).*(-u\b|--updateSnapshot)|pytest.*--snapshot-update
> since: {{TODAY}}
