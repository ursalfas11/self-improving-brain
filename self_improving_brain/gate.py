"""The vault gate: is `brain lint` free of MUST findings, and if not, whose fault is it?

A MUST finding (a secret, a dead link, a duplicate rule id, a check that is not a regex) is what the nightly run
treats as red. Two things went wrong with a plain red/green:

  * Blame. A step that ran in a night that was already red looked guilty of the red. Now the night measures the gate
    before its writing steps and after them: only a cause that is NEW after the steps belongs to them.
  * Silence. A red that no step caused is nobody's job, so it stayed red for weeks and only `brain lint` knew. Now the
    nightly run keeps count of the nights in a row (`.brain/state/gate-red.json`, one file per machine, green clears
    it), and from the second night on `brain`, `brain doctor`, `brain review` and the nightly report say so, with the
    cause and the first thing to do about it.

  causes(findings)       MUST findings -> categories (secret, dead_link, duplicate_id, bad_regex, other)
  measure(v)             run the gate -> {"red", "causes", "count", "line"}
  new_causes(a, b)       what is red in `b` that was not red in `a`
  book(v, res)           the last gate result of a night, nights in a row
  streak_lines(v)        the long-lived red as lines for the status, doctor and review
"""
from __future__ import annotations

import re

from .config import Vault
from .util import load_json, now_iso, save_json, today

STATE = "gate-red.json"
STREAK = 2  # this many nights in a row: a finding of its own
CAUSES = (
    ("secret", r"possible secret"),
    ("dead_link", r"dead link"),
    ("duplicate_id", r"lesson id .* also used"),
    ("bad_regex", r"not a valid regex"),
)
FIX = {
    "secret": "remove it from the note (`brain scan <file>` finds it)",
    "dead_link": "fix or remove the link",
    "duplicate_id": "rename one of the rules",
    "bad_regex": "fix the pattern of that rule",
    "other": "read the finding",
}


def causes(findings) -> list[str]:
    """The categories of the MUST findings, in a stable order. Green -> []."""
    out: list[str] = []
    for f in findings:
        if f.level != "MUST":
            continue
        c = next((name for name, rx in CAUSES if re.search(rx, f.msg)), "other")
        if c not in out:
            out.append(c)
    return sorted(out, key=lambda c: ([n for n, _ in CAUSES] + ["other"]).index(c))


def measure(v: Vault, findings=None) -> dict:
    """Run the gate (or take findings the caller has just made). red = at least one MUST finding."""
    if findings is None:
        from .lint import lint
        findings = lint(v)
    must = [f for f in findings if f.level == "MUST"]
    first = f"{must[0].note}: {must[0].msg}" if must else ""
    return {"red": bool(must), "causes": causes(findings), "count": len(must), "line": first[:200]}


def new_causes(before: dict | None, after: dict) -> list[str]:
    """Causes that came with the steps in between. `before` None (not measured): all of them, as before."""
    if before is None:
        return list(after.get("causes") or [])
    return [c for c in after.get("causes") or [] if c not in (before.get("causes") or [])]


def book(v: Vault, res: dict) -> dict | None:
    """Keep the result of a night's last gate run. Green deletes the record, red counts the nights in a row.
    Returns the record, or None when green."""
    p = v.path("state", STATE)
    if not res.get("red"):
        if p.exists():
            save_json(p, {})
        return None
    st = load_json(p, {})
    st = st if isinstance(st, dict) else {}
    rec = {"since": st.get("since") or today(), "nights": int(st.get("nights") or 0) + 1, "t": now_iso(),
           "causes": res.get("causes") or ["other"], "count": int(res.get("count") or 0), "line": res.get("line", "")}
    save_json(p, rec)
    return rec


def streak_lines(v: Vault) -> list[str]:
    """The gate was red in more than one night in a row: it will not heal itself. One line, with the way out."""
    st = load_json(v.path("state", STATE), {})
    if not isinstance(st, dict) or int(st.get("nights") or 0) < STREAK:
        return []
    cs = [c for c in st.get("causes") or ["other"] if isinstance(c, str)]
    return [f"The vault has had MUST findings for {st['nights']} nights in a row (since {st.get('since', '?')}; "
            f"{', '.join(cs)}): {FIX.get(cs[0] if cs else 'other', FIX['other'])} → brain lint"]
