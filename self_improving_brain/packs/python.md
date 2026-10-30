---
name: pack-python
description: Python habits agents get wrong - bare except, mutable defaults, print debugging left behind, installing into the system interpreter.
type: resource
pack: python
pack_version: 1
updated: {{TODAY}}
---
# Pack: python

Lessons for Python code. Each `check:` looks at what the agent writes into a .py file.
Part of [[hub-brain]]. Installed with `brain pack add python`.

> [!lesson]- L-python-no-bare-except
> rule: Never write a bare `except:` or swallow errors with `except Exception: pass` - catch the specific exception and handle or re-raise it.
> mechanism: reflex
> paths: \.py$
> check: (?m)^\s*except\s*:|except\s+(Exception|BaseException)(\s+as\s+\w+)?\s*:\s*\n\s*pass\b
> since: {{TODAY}}

> [!lesson]- L-python-no-mutable-default
> rule: Do not use a mutable default argument (`=[]`, `={}`) - default to None and create the object inside the function.
> mechanism: reflex
> paths: \.py$
> check: def\s+\w+\([^)]*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))
> since: {{TODAY}}

> [!lesson]- L-python-venv-only
> rule: Install packages into the project's virtualenv (uv, poetry, .venv) - never with sudo pip or --break-system-packages.
> mechanism: guard
> commands: (sudo\s+pip|pip3?\s+install\b.*--break-system-packages|sudo\s+python3?\s+-m\s+pip)
> since: {{TODAY}}

> [!lesson]- L-python-no-debug-leftovers
> rule: Remove breakpoint(), pdb and debug prints before you finish - use logging for anything that should stay.
> mechanism: reflex
> paths: \.py$
> check: \bbreakpoint\(\)|\bimport\s+i?pdb\b|\bpdb\.set_trace\(\)
> since: {{TODAY}}
