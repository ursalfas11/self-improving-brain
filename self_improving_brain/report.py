"""`brain report`: is your agent getting better? A report card in the terminal, or as a picture to share.

The terminal version may name your lessons. The shareable card (`--public`, an SVG in Open Graph
size) and the badge carry numbers only: no note names, no rules, no quotes, no paths.
"""
from __future__ import annotations

from collections import Counter
from xml.sax.saxutils import escape

from .config import Vault

REPO = "github.com/ursalfas11/self-improving-brain"


def numbers(st: dict) -> dict:
    """The few numbers worth showing, from `ui.state()`; None where there is no data yet."""
    curve = [r for r in st.get("curve") or [] if r.get("messages")]
    first, last = (curve[0], curve[-1]) if len(curve) >= 2 else (None, None)
    change = None
    if first and first["per100"]:
        change = round(100 * (last["per100"] - first["per100"]) / first["per100"])
    mech = Counter(l["mechanism"] for l in st.get("lessons") or [] if not l.get("silent"))
    r = st.get("replay") or {}
    checks = list((st.get("checks") or {}).values())
    vio = None
    if len(checks) >= 2 and checks[0].get("checked") and checks[-1].get("checked"):
        a = checks[0]["violations"] / checks[0]["checked"]
        b = checks[-1]["violations"] / checks[-1]["checked"]
        vio = round(100 * (b - a) / a) if a else None
    return {
        "weeks": len(curve), "first": first, "last": last, "change": change,
        "messages": sum(x["messages"] for x in curve), "corrections": sum(x["corrections"] for x in curve),
        "curve": [x["per100"] for x in curve],
        "lessons": sum(mech.values()), "mechanisms": dict(mech),
        "replay": {k: r.get(k) for k in ("corrections", "caught", "would_ask", "caught_pct", "unseen", "unseen_caught",
                                         "learned_from", "sessions", "days")} if r else None,
        "violations_change": vio, "precision": (st.get("recall") or {}).get("precision"),
    }


def _pct(x) -> str:
    return "–" if x is None else (f"{x:+d} %".replace("-", "−"))


def text(st: dict) -> str:
    n = numbers(st)
    out = [f"REPORT CARD · {st['vault']['name']} · {n['weeks']} weeks"]
    if n["change"] is None:
        out.append("  corrections per 100 messages: not enough data yet (two weeks of sessions)")
    else:
        out.append(f"  corrections per 100 messages: {n['first']['per100']} ({n['first']['week']}) → "
                   f"{n['last']['per100']} ({n['last']['week']})  {_pct(n['change'])}")
    m = n["mechanisms"]
    out.append(f"  lessons: {n['lessons']} ({', '.join(f'{v} {k}' for k, v in sorted(m.items(), key=lambda x: -x[1]))})")
    r = n["replay"]
    if r and r.get("corrections"):
        out.append(f"  replay: today's lessons fire before {r['caught']} of {r['corrections']} past corrections, "
                   f"{r['would_ask']} by asking first; of the {r.get('unseen') or 0} that taught no rule, "
                   f"{r.get('unseen_caught') or 0} are caught")
    else:
        out.append("  replay: not run yet (the nightly run does it)")
    if n["violations_change"] is not None:
        out.append(f"  rule-check violations per checked action: {_pct(n['violations_change'])}")
    if n["precision"] is not None:
        out.append(f"  recall precision: {round(100 * n['precision'])} % of injected notes were opened")
    weak = [l for l in st.get("lessons") or [] if (l.get("health") or {}).get("verdict", "ok") != "ok"]
    if weak:
        out.append("NEEDS A LOOK")
        out += [f"  {l['id']}: {l['health']['verdict']}" for l in weak[:6]]
    out.append("→ share the numbers (no notes, no rules): brain report --public -o report.svg")
    return "\n".join(out)


def _spark(vals: list[float], x: int, y: int, w: int, h: int) -> str:
    if len(vals) < 2:
        return ""
    hi, lo = max(vals), min(vals)
    span = (hi - lo) or 1
    pts = [(x + i * w / (len(vals) - 1), y + h - (v - lo) / span * h) for i, v in enumerate(vals)]
    line = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
    area = f"M{pts[0][0]:.1f},{y + h} L" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f" L{pts[-1][0]:.1f},{y + h} Z"
    ex, ey = pts[-1]
    return (f'<path d="{area}" fill="url(#g)" opacity=".35"/>'
            f'<polyline points="{line}" fill="none" stroke="#7cf7d4" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="7" fill="#7cf7d4"/>')


def card(st: dict) -> str:
    """1200×630 SVG with numbers only."""
    n = numbers(st)
    big = _pct(n["change"]) if n["change"] is not None else "–"
    sub = (f"corrections per 100 messages · {n['first']['per100']} → {n['last']['per100']} over {n['weeks']} weeks"
           if n["change"] is not None else "corrections per 100 messages · not enough weeks yet")
    r = n["replay"] or {}
    m = n["mechanisms"]
    tiles = [
        (str(n["lessons"]), "lessons", f"{m.get('guard', 0)} guards · {m.get('reflex', 0) + m.get('skill', 0)} reflexes · {m.get('always', 0)} always"),
        (f"{r['caught_pct']} %" if r.get("caught_pct") is not None else "–", "of unseen mistakes caught",
         f"{r.get('unseen_caught') or 0} of {r.get('unseen') or 0} that taught no rule" if r.get("corrections")
         else "run `brain replay`"),
        (str(r.get("would_ask")) if r.get("corrections") else "–", "risky actions flagged",
         "a guard would have asked first"),
    ]
    t = []
    for i, (val, label, hint) in enumerate(tiles):
        x = 64 + i * 364
        t.append(f'<rect x="{x}" y="404" width="340" height="150" rx="18" fill="#0d1124" stroke="#2b3366"/>'
                 f'<text x="{x + 28}" y="468" class="v">{escape(val)}</text>'
                 f'<text x="{x + 28}" y="502" class="l">{escape(label)}</text>'
                 f'<text x="{x + 28}" y="532" class="h">{escape(hint)}</text>')
    week = n["last"]["week"] if n["last"] else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="Agent report card: {escape(big)} corrections per 100 messages">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7cf7d4"/><stop offset="1" stop-color="#7cf7d4" stop-opacity="0"/></linearGradient>
<radialGradient id="bg" cx=".85" cy="0" r="1"><stop offset="0" stop-color="#1b2150"/><stop offset="1" stop-color="#0b1020"/></radialGradient></defs>
<style>text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;fill:#e8ebff}}
.k{{font-size:22px;fill:#aab2e0;letter-spacing:.08em}}.big{{font-size:150px;font-weight:800;fill:#7cf7d4;letter-spacing:-.03em}}
.s{{font-size:26px;fill:#c9d1ff}}.v{{font-size:52px;font-weight:750}}.l{{font-size:22px;fill:#c9d1ff}}.h{{font-size:17px;fill:#7d86b8}}
.f{{font-size:20px;fill:#7d86b8}}</style>
<rect width="1200" height="630" fill="url(#bg)"/>
<text x="64" y="84" class="k">MY CODING AGENT · REPORT CARD{(" · " + escape(week)) if week else ""}</text>
<text x="56" y="250" class="big">{escape(big)}</text>
<text x="64" y="304" class="s">{escape(sub)}</text>
{_spark(n["curve"], 760, 130, 376, 170)}
{"".join(t)}
<text x="64" y="600" class="f">self-improving-brain · {REPO}</text>
</svg>
'''


def badge(st: dict) -> str:
    n = numbers(st)
    val = _pct(n["change"]) if n["change"] is not None else "learning"
    left, right = "agent corrections", val
    lw, rw = 7 * len(left) + 16, 8 * len(right) + 16
    color = "#0f6b5a" if (n["change"] or 0) <= 0 else "#8a5a00"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{lw + rw}" height="20" role="img" aria-label="{left}: {escape(right)}">'
            f'<rect width="{lw}" height="20" rx="3" fill="#2b3366"/><rect x="{lw}" width="{rw}" height="20" rx="3" fill="{color}"/>'
            f'<rect x="{lw}" width="4" height="20" fill="{color}"/>'
            f'<g font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" fill="#fff">'
            f'<text x="8" y="14">{left}</text><text x="{lw + 8}" y="14">{escape(right)}</text></g></svg>\n')


def build(v: Vault) -> dict:
    from .ui import state
    return state(v)
