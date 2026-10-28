#!/usr/bin/env python3
"""Build the animated README images:  python3 docs/assets/src/build.py   (writes docs/assets/*.svg)

Plain SVG + CSS, plus SMIL for things that travel along a path. No scripts, so GitHub renders them as
images. Every animation loops; with `prefers-reduced-motion` each image shows its complete final frame.
"""
from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
TEAL, PERI, PINK, GOLD = "#7cf7d4", "#8b9bff", "#f08cff", "#ffd479"
TXT, SOFT, DIM, PALE = "#e8ebff", "#aab2e0", "#7d86b8", "#c9d1ff"
CARD, LINE = "#141a38", "#2b3366"
REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}.fx{display:none}}"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Timeline:
    """CSS keyframes on one looping clock of `total` seconds."""

    def __init__(self, total: float, prefix: str = "a"):
        self.total, self.prefix, self.css, self.n = total, prefix, [], 0

    def anim(self, stops, extra: str = "") -> str:
        """stops: [(seconds, "css", easing to the next stop)]. Returns the class name."""
        self.n += 1
        name = f"{self.prefix}{self.n}"
        frames = []
        for t, props, *ease in stops:
            pct = max(0.0, min(100.0, t / self.total * 100))
            frames.append(f"{pct:.3f}%{{{props};animation-timing-function:{ease[0] if ease else 'steps(1,end)'}}}")
        self.css.append(f"@keyframes {name}{{{''.join(frames)}}}"
                        f".{name}{{animation:{name} {self.total:.2f}s infinite{';' + extra if extra else ''}}}")
        return name

    def end(self) -> float:
        return self.total - 0.6

    def appear(self, t: float, fade: float = 0.15) -> str:
        e = self.end()
        return self.anim([(0, "opacity:0"), (t, "opacity:0", "ease-out"), (t + fade, "opacity:1"),
                          (e, "opacity:1", "linear"), (e + 0.35, "opacity:0"), (self.total, "opacity:0")])

    def window(self, t0: float, t1: float, peak: float = 1.0, fade: float = 0.25) -> str:
        """Visible from t0 to t1, faded in and out."""
        return self.anim([(0, "opacity:0"), (t0, "opacity:0", "ease-out"), (t0 + fade, f"opacity:{peak}"),
                          (t1 - fade, f"opacity:{peak}", "ease-in"), (t1, "opacity:0"), (self.total, "opacity:0")])

    def shift(self, t: float, d: float, dx: float, ease: str = "linear") -> str:
        return self.anim([(0, "transform:translateX(0px)"), (t, "transform:translateX(0px)", ease),
                          (t + d, f"transform:translateX({dx:.1f}px)"), (self.total, f"transform:translateX({dx:.1f}px)")])

    def grow(self, t: float, d: float = 0.9, axis: str = "X") -> str:
        s = f"scale{axis}"
        return self.anim([(0, f"transform:{s}(0)"), (t, f"transform:{s}(0)", "cubic-bezier(.2,.8,.2,1)"),
                          (t + d, f"transform:{s}(1)"), (self.total, f"transform:{s}(1)")])


def svg(w: int, h: int, label: str, style: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(label)}">\n<style>\n{style}\n</style>\n{body}\n</svg>\n')


# --- terminal (demo + setup) ----------------------------------------------------------------------

def terminal(name: str, title: str, label: str, rows: list[dict], W: int = 960, fs: int = 15,
             hold: float = 4.5, progress: list[tuple[str, int]] | None = None) -> str:
    """rows: dicts with kind head|out|typed|flash|bar|step|gap, text fields and an optional pause."""
    cw, LH, PAD, TOP = fs * 0.6, 26, 28, 84
    t, sched = 0.0, []
    for r in rows:
        t += r.get("pause", 0.5)
        dur = 0.0
        if r["kind"] == "typed":
            dur = min(1.7, max(0.45, 0.03 * len(r["body"])))
        elif r["kind"] == "step":
            dur = r.get("wait", 0.65)
        sched.append((t, dur))
        t += dur
    total = t + hold
    tl = Timeline(total)
    H = TOP + LH * len(rows) + 10
    bg = "#0d1124"

    def text(x, y, s, color, extra=""):
        return (f'<text class="m" x="{x:.1f}" y="{y}" fill="{color}" textLength="{len(s) * cw:.1f}" '
                f'lengthAdjust="spacing"{extra}>{esc(s)}</text>')

    body = [f'<rect width="{W}" height="{H}" rx="14" fill="{bg}"/>',
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="none" stroke="{LINE}"/>',
            '<circle cx="24" cy="24" r="6.5" fill="#ff5f57"/><circle cx="46" cy="24" r="6.5" fill="#febc2e"/>'
            '<circle cx="68" cy="24" r="6.5" fill="#28c840"/>',
            f'<text class="t" x="92" y="29" fill="{DIM}" font-size="13">{esc(title)}</text>',
            f'<line x1="0" y1="46" x2="{W}" y2="46" stroke="#1f2550"/>',
            f'<clipPath id="{name}-c"><rect x="1" y="47" width="{W - 2}" height="{H - 48}" rx="13"/></clipPath>',
            f'<g clip-path="url(#{name}-c)">']
    starts = [s for s, _ in sched]
    for i, (r, (t0, dur)) in enumerate(zip(rows, sched)):
        y = TOP + i * LH
        k = r["kind"]
        nxt = next((s for s in starts[i + 1:] if s > t0), tl.end())
        if k == "gap":
            continue
        if k in ("head", "out"):
            color = PERI if k == "head" else r.get("color", TXT)
            weight = ' font-weight="700"' if r.get("bold") else ""
            body.append(f'<g class="{tl.appear(t0)}">{text(PAD, y, r["text"], color, weight)}</g>')
        elif k == "flash":
            w = len(r["text"]) * cw + 20
            body.append(f'<rect class="{tl.anim([(0, "opacity:0"), (t0, "opacity:0", "ease-out"), (t0 + 0.25, "opacity:.45", "ease-in-out"), (t0 + 1.7, "opacity:.15"), (tl.end(), "opacity:.15", "linear"), (tl.end() + 0.35, "opacity:0"), (total, "opacity:0")])}" '
                        f'x="{PAD - 10}" y="{y - 18}" width="{w:.1f}" height="25" rx="6" fill="{PINK}" style="opacity:.15"/>')
            body.append(f'<g class="{tl.appear(t0)}">{text(PAD, y, r["text"], r.get("color", PINK), " font-weight=\"700\"")}</g>')
        elif k == "typed":
            pre, rest = r["prefix"], r["body"]
            x1 = PAD + len(pre) * cw
            dist = len(rest) * cw + 2 * cw
            body.append(f'<g class="{tl.appear(t0, 0.05)}">{text(PAD, y, pre, r.get("pcolor", TEAL))}'
                        f'{text(x1, y, rest, r.get("color", TXT))}</g>')
            # the cover slides off the text like a typing head; with reduced motion it sits beyond the text
            body.append(f'<rect class="{tl.shift(t0, dur, dist)}" x="{x1:.1f}" y="{y - 17}" width="{dist:.1f}" '
                        f'height="24" fill="{bg}" style="transform:translateX({dist:.1f}px)"/>')
            # a block cursor rides along until the next line starts
            body.append(f'<g class="fx {tl.window(t0, nxt, 1, 0.05)}"><rect class="{tl.shift(t0, dur, len(rest) * cw)}" '
                        f'x="{x1:.1f}" y="{y - 14}" width="{cw:.1f}" height="18" fill="{TEAL}"/></g>')
        elif k == "bar":
            body.append(f'<g class="{tl.appear(t0)}">{text(PAD, y, r["text"], TXT)}'
                        f'<rect class="{tl.grow(t0 + 0.1, 0.9)}" x="{PAD + len(r["text"]) * cw + 6:.1f}" y="{y - 13}" '
                        f'width="{r["width"]}" height="16" rx="3" fill="{r["color"]}" opacity=".9" '
                        f'style="transform-box:fill-box;transform-origin:0 50%"/></g>')
        elif k == "step":
            col = PAD + r.get("col", 34) * cw
            body.append(f'<g class="{tl.appear(t0)}">{text(PAD, y, r["text"], TXT)}</g>')
            body.append(f'<g class="fx {tl.window(t0, t0 + dur, 1, 0.08)}">{text(col, y, "…", DIM)}</g>')
            body.append(f'<g class="{tl.appear(t0 + dur, 0.2)}">{text(col, y, "✓", TEAL, " font-weight=\"700\"")}'
                        f'{text(col + 2 * cw, y, r["result"], r.get("color", SOFT))}</g>')
    body.append("</g>")
    # idle cursor after the last line
    yc = TOP + len(rows) * LH - 4
    body.append(f'<g class="{tl.appear(sched[-1][0] + sched[-1][1] + 0.3)}"><rect class="cur" x="{PAD}" y="{yc - 14}" '
                f'width="{cw:.1f}" height="18" fill="{TEAL}"/></g>')
    if progress:
        x0, x1 = W - 330, W - 60
        n = len(progress)
        xs = [x0 + (x1 - x0) * j / (n - 1) for j in range(n)]
        body.append(f'<line x1="{x0}" y1="24" x2="{x1}" y2="24" stroke="{LINE}" stroke-width="3" stroke-linecap="round"/>')
        stops = [(0, "transform:scaleX(0)")]
        for j, (_, row) in enumerate(progress):
            st = sched[row][0]
            stops.append((st, f"transform:scaleX({j / (n - 1):.3f})", "cubic-bezier(.4,0,.2,1)"))
            stops.append((st + 0.6, f"transform:scaleX({j / (n - 1):.3f})"))
        stops = sorted(stops, key=lambda s: s[0])
        stops.append((tl.end(), "transform:scaleX(1)", "ease-in"))
        stops.append((tl.end() + 0.35, "transform:scaleX(0)"))
        stops.append((total, "transform:scaleX(0)"))
        body.append(f'<line class="{tl.anim(stops)}" x1="{x0}" y1="24" x2="{x1}" y2="24" stroke="{TEAL}" stroke-width="3" '
                    f'stroke-linecap="round" style="transform-box:fill-box;transform-origin:0 50%"/>')
        for j, ((lab, row), x) in enumerate(zip(progress, xs)):
            st = sched[row][0]
            body.append(f'<circle cx="{x:.1f}" cy="24" r="6" fill="{bg}" stroke="{LINE}" stroke-width="2"/>')
            body.append(f'<circle class="{tl.appear(st + 0.45, 0.25)}" cx="{x:.1f}" cy="24" r="6" fill="{TEAL}"/>')
            body.append(f'<text class="t" x="{x:.1f}" y="42" fill="{SOFT}" font-size="11" text-anchor="middle">{esc(lab)}</text>')
    style = "\n".join([
        f".m{{font-family:{MONO};font-size:{fs}px;white-space:pre}}.t{{font-family:{FONT}}}",
        "@keyframes cur{0%,49%{opacity:1}50%,100%{opacity:0}}.cur{animation:cur 1s steps(1) infinite}",
        *tl.css, REDUCED])
    return svg(W, H, label, style, "\n".join(body))


def demo() -> str:
    you = dict(kind="typed", prefix="you   › ", pcolor=TEAL)
    cmd = dict(kind="typed", prefix="$ ", pcolor=GOLD, color=GOLD)
    rows = [
        dict(kind="head", text="── Tuesday ───────────────────────────────────────────────────", pause=0.3),
        dict(you, body="the checkout test is flaky, fix it", pause=0.5),
        dict(kind="out", text="agent › Added a retry around the test. Done ✓", color=PALE, pause=0.7),
        dict(you, body="no — that's not a fix. find the race instead of adding retries", pause=0.9),
        dict(kind="gap", pause=0),
        dict(kind="head", text="── that night, on your laptop ─────────────────────────────────", pause=0.7),
        dict(cmd, body="brain learn", pause=0.5),
        dict(kind="out", text="scanned 14 transcripts · 212 user messages · 9 corrections · 3 new drafts", color=DIM, pause=0.5),
        dict(cmd, body='brain approve d-ca0e5af6 --rule "Fix flaky tests at the root; never add retries."', pause=0.8),
        dict(kind="out", text="✓ lesson L-fix-flaky-tests-root (reflex) written to note testing", color=TEAL, pause=0.5),
        dict(kind="gap", pause=0),
        dict(kind="head", text="── Friday ────────────────────────────────────────────────────", pause=0.7),
        dict(you, body="the login test is flaky again, can you add a retry?", pause=0.5),
        dict(kind="flash", text="brain › LESSON L-fix-flaky-tests-root: Fix flaky tests at the root; never add retries.", pause=0.6),
        dict(kind="out", text="agent › Before any retry, let me find the race… shared session fixture. Fixed at the root.", color=PALE, pause=1.1),
        dict(kind="gap", pause=0),
        dict(cmd, body="brain stats", pause=0.7),
        dict(kind="out", text="CORRECTIONS per 100 user messages   (example)", color=DIM, pause=0.4),
        dict(kind="bar", text="  W41  12.4", width=372, color=PINK, pause=0.3),
        dict(kind="bar", text="  W48   5.3", width=159, color=TEAL, pause=0.5),
    ]
    return terminal("demo", "your agent · one week", "Demo: on Tuesday the user corrects the agent; that night brain learn "
                    "turns it into a lesson; on Friday the lesson fires before the agent repeats the mistake",
                    rows, progress=[("Tuesday", 0), ("night", 5), ("Friday", 11)])


def setup_demo() -> str:
    rows = [
        dict(kind="typed", prefix="$ ", pcolor=GOLD, color=GOLD,
             body="curl -fsSL https://raw.githubusercontent.com/ursalfas11/self-improving-brain/main/install.sh | sh", pause=0.4),
        dict(kind="out", text="self-improving-brain setup — everything stays on this machine unless you say otherwise",
             color=PERI, bold=True, pause=0.6),
        dict(kind="step", text="1/7  Your vault", result="~/brain · own git history · secret scan on commit", pause=0.35),
        dict(kind="step", text="2/7  Connect your agents", result="Claude Code · Codex · Gemini CLI", pause=0.3),
        dict(kind="step", text="3/7  The brain command", result="linked ~/.local/bin/brain", pause=0.3),
        dict(kind="step", text="4/7  Learn from past sessions", result="127 sessions · 64 corrections → 59 lesson candidates",
             color=PINK, wait=1.2, pause=0.3),
        dict(kind="step", text="5/7  Smarter judging (optional)", result="claude, your existing login · no API key stored", pause=0.3),
        dict(kind="step", text="6/7  Backup", result="github.com/you/brain  (private)", color=TEAL, wait=0.9, pause=0.3),
        dict(kind="step", text="7/7  Nightly self-improvement", result="every night at 03:17", pause=0.3),
        dict(kind="gap", pause=0),
        dict(kind="out", text="Done. Open a new agent session — the brain is in it from the first prompt.",
             color=TXT, bold=True, pause=0.6),
    ]
    return terminal("setup", "brain setup · about a minute", "brain setup in seven steps: vault, agents, command, "
                    "learning from past sessions, optional LLM judge, private GitHub backup, nightly run", rows, W=980, hold=5)


# --- banner -------------------------------------------------------------------------------------

def banner() -> str:
    nodes = {"A": (-70, -40), "B": (-28, -62), "C": (30, -70), "D": (70, -30), "E": (40, 10), "F": (80, 50),
             "G": (-50, 20), "H": (-10, 40), "I": (-30, 80), "J": (30, 72)}
    edges = ["AB", "BC", "CD", "DE", "EF", "AG", "GH", "HE", "HI", "BH", "CE", "IJ", "JF"]
    routes = [("ABCDEF", 3.0, 0.0), ("GHED", 2.2, 0.9), ("IJFEC", 2.8, 1.7), ("BHIJ", 2.4, 2.6), ("AGHEF", 2.9, 3.4)]
    p = lambda k: f"{nodes[k][0]},{nodes[k][1]}"
    edge_svg = "".join(f'<line x1="{nodes[a][0]}" y1="{nodes[a][1]}" x2="{nodes[b][0]}" y2="{nodes[b][1]}"/>' for a, b in edges)
    node_svg = "".join(f'<circle class="node" cx="{x}" cy="{y}" r="5"/>' for k, (x, y) in nodes.items() if k not in "CHE")
    lit = "".join(f'<circle class="lit" cx="{x}" cy="{y}" r="5.5" style="animation-delay:{(x + 80) / 160 * 1.4:.2f}s"/>'
                  for x, y in nodes.values())
    signals = "".join(
        f'<g opacity="0"><circle r="7" fill="{TEAL}" opacity=".25"/><circle r="3.2" fill="#eafffa"/>'
        f'<animateMotion dur="{d}s" begin="{b}s" repeatCount="indefinite" path="M{" L".join(p(k) for k in r)}"/>'
        f'<animate attributeName="opacity" dur="{d}s" begin="{b}s" repeatCount="indefinite" values="0;1;1;0" keyTimes="0;.12;.82;1"/></g>'
        for r, d, b in routes)
    stars = [(300, 54), (520, 36), (760, 62), (985, 44), (1190, 70), (1245, 300), (1150, 392), (905, 388), (640, 398),
             (322, 384), (38, 58), (52, 372), (1240, 160), (700, 24)]
    star_svg = "".join(f'<circle class="tw" cx="{x}" cy="{y}" r="{1.2 + (i % 3) * .35:.2f}" fill="{PALE}" '
                       f'style="animation-delay:{(i * .37) % 3.2:.2f}s"/>' for i, (x, y) in enumerate(stars))
    chips = [("Claude Code", 132), ("Codex", 84), ("Gemini CLI", 118), ("Cursor", 84), ("Grok", 72), ("Aider", 78)]
    chip_svg, x = [], 362
    for i, (name, w) in enumerate(chips):
        chip_svg.append(f'<g transform="translate({x} 270)"><rect width="{w}" height="34" rx="17" fill="#ffffff" fill-opacity=".08" '
                        f'stroke="#ffffff" stroke-opacity=".15"/><rect class="chip" width="{w}" height="34" rx="17" fill="{TEAL}" '
                        f'fill-opacity=".14" stroke="{TEAL}" stroke-opacity=".9" style="animation-delay:{i * .32:.2f}s"/>'
                        f'<text x="{w / 2:.0f}" y="22" text-anchor="middle">{esc(name)}</text></g>')
        x += w + 10
    curve = "M0,22 C30,30 40,60 70,70 C100,80 110,105 140,112 C165,118 180,128 200,132"
    counters = [("12.4", PINK, "c1"), ("9.8", "#c39cff", "c2"), ("7.1", PERI, "c3"), ("5.3", TEAL, "c4")]
    counter_svg = "".join(f'<text class="m {c}" x="0" y="-22" font-size="30" font-weight="800" fill="{col}">{v}</text>'
                          for v, col, c in counters)
    style = f"""
.t{{font-family:{FONT}}}.m{{font-family:{MONO}}}
.edge{{stroke:{PERI};stroke-opacity:.45;stroke-width:1.6}}.node{{fill:{PALE}}}
.hot{{fill:{TEAL};animation:pulse 2.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}
.hot2{{animation-delay:.8s}}.hot3{{animation-delay:1.6s}}
@keyframes pulse{{0%,100%{{opacity:.55;transform:scale(1)}}50%{{opacity:1;transform:scale(1.6)}}}}
.lit{{fill:{TEAL};opacity:0;animation:lit 5s ease-in-out infinite}}
@keyframes lit{{0%,100%{{opacity:0}}10%{{opacity:.95}}26%{{opacity:0}}}}
.alt{{opacity:0;animation:alt 7s ease-in-out infinite}}@keyframes alt{{0%,100%{{opacity:0}}50%{{opacity:.9}}}}
.breath{{animation:breath 5s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}
@keyframes breath{{0%,100%{{transform:scale(1);opacity:.8}}50%{{transform:scale(1.1);opacity:1}}}}
.tw{{opacity:.2;animation:tw 3.2s ease-in-out infinite}}@keyframes tw{{0%,100%{{opacity:.15}}50%{{opacity:.85}}}}
.chip{{opacity:0;animation:chip 6s ease-in-out infinite}}@keyframes chip{{0%,100%{{opacity:0}}5%{{opacity:1}}16%{{opacity:0}}}}
.shine{{animation:shine 6s cubic-bezier(.45,0,.25,1) infinite}}
@keyframes shine{{0%{{transform:translateX(-240px)}}42%,100%{{transform:translateX(620px)}}}}
.curve{{stroke-dasharray:100;animation:curve 8s infinite}}
@keyframes curve{{0%{{stroke-dashoffset:100;opacity:1;animation-timing-function:cubic-bezier(.3,.6,.3,1)}}
40%{{stroke-dashoffset:0;opacity:1}}88%{{stroke-dashoffset:0;opacity:1;animation-timing-function:linear}}96%,100%{{stroke-dashoffset:0;opacity:0}}}}
.c1,.c2,.c3{{opacity:0}}.c1{{animation:c1 8s steps(1,end) infinite}}.c2{{animation:c2 8s steps(1,end) infinite}}
.c3{{animation:c3 8s steps(1,end) infinite}}.c4{{animation:c4 8s steps(1,end) infinite}}
@keyframes c1{{0%{{opacity:1}}6.7%,100%{{opacity:0}}}}@keyframes c2{{0%{{opacity:0}}6.7%{{opacity:1}}13.9%,100%{{opacity:0}}}}
@keyframes c3{{0%{{opacity:0}}13.9%{{opacity:1}}25%,100%{{opacity:0}}}}@keyframes c4{{0%{{opacity:0}}25%{{opacity:1}}96%,100%{{opacity:0}}}}
.badge{{animation:badge 8s infinite;transform-box:fill-box;transform-origin:center}}
@keyframes badge{{0%,38%{{opacity:0;transform:scale(.6)}}46%{{opacity:1;transform:scale(1.08)}}50%,88%{{opacity:1;transform:scale(1)}}96%,100%{{opacity:0;transform:scale(1)}}}}
{REDUCED}"""
    body = f"""<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1020"/><stop offset=".55" stop-color="#151a3a"/><stop offset="1" stop-color="#2a1245"/></linearGradient>
<linearGradient id="accent" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{TEAL}"/><stop offset=".5" stop-color="{PERI}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="accent2" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{PINK}"/><stop offset=".5" stop-color="{TEAL}"/><stop offset="1" stop-color="{PERI}"/></linearGradient>
<linearGradient id="shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity="1"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<radialGradient id="glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{PERI}" stop-opacity=".55"/><stop offset="1" stop-color="{PERI}" stop-opacity="0"/></radialGradient>
<pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="#ffffff" stroke-opacity=".035"/></pattern>
<clipPath id="tag"><text class="t" x="362" y="232" font-size="25" font-weight="700">This brain gets smarter with every one.</text></clipPath>
</defs>
<rect width="1280" height="420" rx="24" fill="url(#bg)"/><rect width="1280" height="420" rx="24" fill="url(#grid)"/>
{star_svg}
<g transform="translate(170 210)">
<circle class="breath" r="150" fill="url(#glow)"/>
<path d="M-92,-30 C-118,-92 -48,-128 -10,-104 C10,-140 88,-128 92,-74 C140,-62 140,8 104,26 C124,70 76,112 34,96 C10,128 -58,124 -70,86 C-124,84 -136,8 -92,-30 Z" fill="none" stroke="url(#accent)" stroke-width="3" stroke-linejoin="round" opacity=".9"/>
<path class="alt" d="M-92,-30 C-118,-92 -48,-128 -10,-104 C10,-140 88,-128 92,-74 C140,-62 140,8 104,26 C124,70 76,112 34,96 C10,128 -58,124 -70,86 C-124,84 -136,8 -92,-30 Z" fill="none" stroke="url(#accent2)" stroke-width="3" stroke-linejoin="round"/>
<path d="M-6,-100 C-20,-50 20,-30 0,10 C-18,46 16,70 4,100" fill="none" stroke="url(#accent)" stroke-width="2" opacity=".5"/>
<g class="edge">{edge_svg}</g>
{node_svg}{lit}
<circle class="hot" cx="30" cy="-70" r="6"/><circle class="hot hot2" cx="-10" cy="40" r="6"/><circle class="hot hot3" cx="40" cy="10" r="6"/>
<g class="fx">{signals}</g>
</g>
<text class="t" x="360" y="150" font-size="64" font-weight="800" fill="#ffffff" letter-spacing="-1.5">self-improving-brain</text>
<text class="t" x="362" y="198" font-size="25" fill="{PALE}">Your coding agent forgets everything between sessions.</text>
<text class="t" x="362" y="232" font-size="25" font-weight="700" fill="url(#accent)">This brain gets smarter with every one.</text>
<g class="fx" clip-path="url(#tag)"><rect class="shine" x="362" y="200" width="210" height="44" fill="url(#shine)"/></g>
<g class="m" font-size="15" fill="{TXT}">{"".join(chip_svg)}</g>
<text class="m" x="362" y="350" font-size="16" fill="#9aa3d6">no server · no API key · no vector DB · zero dependencies · plain Markdown</text>
<g transform="translate(1030 110)">
<text class="m" x="0" y="0" font-size="13" fill="#9aa3d6">mistakes / 100 msgs</text>
{counter_svg}
<line x1="0" y1="150" x2="200" y2="150" stroke="#ffffff" stroke-opacity=".2"/>
<path class="curve" pathLength="100" d="{curve}" fill="none" stroke="url(#accent)" stroke-width="4" stroke-linecap="round"/>
<circle cx="0" cy="22" r="5" fill="{PINK}"/><circle cx="200" cy="132" r="5" fill="{TEAL}"/>
<g class="fx" opacity="0"><circle r="9" fill="{TEAL}" opacity=".3"/><circle r="4.5" fill="#eafffa"/>
<animateMotion dur="8s" repeatCount="indefinite" path="{curve}" keyPoints="0;1;1;1" keyTimes="0;.4;.96;1" calcMode="spline" keySplines=".3 .6 .3 1;0 0 1 1;0 0 1 1"/>
<animate attributeName="opacity" dur="8s" repeatCount="indefinite" values="1;1;0;0" keyTimes="0;.88;.96;1"/></g>
<g class="badge"><rect x="128" y="76" width="72" height="26" rx="13" fill="{TEAL}" fill-opacity=".16" stroke="{TEAL}" stroke-opacity=".7"/>
<text class="m" x="164" y="94" font-size="14" font-weight="700" fill="{TEAL}" text-anchor="middle">−57 %</text></g>
<text class="m" x="0" y="172" font-size="13" fill="#9aa3d6">week 1</text>
<text class="m" x="200" y="172" font-size="13" fill="#9aa3d6" text-anchor="end">week 8</text>
</g>"""
    return svg(1280, 420, "self-improving-brain — your coding agent forgets, this brain gets smarter every session", style, body)


# --- learning loop ------------------------------------------------------------------------------

def loop() -> str:
    T = 10.0
    tl = Timeline(T, "l")
    cards = [  # (x, y, w, h, title, mono line, text line)
        (60, 40, 250, 118, "1 · You work", None, "Claude Code, Codex, Gemini …", 'you correct it: "no, …"'),
        (790, 40, 250, 118, "2 · It learns", "brain learn", "corrections → lesson drafts", None),
        (850, 300, 210, 118, "3 · You OK it", "brain approve", "nothing without you", None),
        (330, 360, 440, 92, "4 · Right rule, right moment", None, "prompt words · tool · shell command · path  →  reflex or guard", None),
        (40, 300, 230, 118, "5 · It measures", "brain stats", "mistakes ↓ · recall tunes itself", None),
    ]
    arrows = ["M310,95 C450,70 650,70 788,95", "M960,160 C985,210 985,250 965,298", "M850,380 C820,400 800,405 772,405",
              "M330,405 C300,405 285,400 272,385", "M150,300 C135,240 140,200 170,160"]
    out = [f'<rect width="1100" height="470" rx="22" fill="#0b1020"/>',
           f'<rect width="1100" height="470" rx="22" fill="url(#lgrid)"/>',
           '<circle cx="550" cy="235" r="92" fill="#141a38" stroke="url(#la)" stroke-width="2.5"/>',
           f'<circle class="{tl.anim([(0, "opacity:0;transform:scale(1)"), (4.35, "opacity:.9;transform:scale(1)", "cubic-bezier(.2,.7,.3,1)"), (5.6, "opacity:0;transform:scale(1.45)"), (T, "opacity:0;transform:scale(1.45)")], "transform-box:fill-box;transform-origin:center")}" '
           f'cx="550" cy="235" r="92" fill="none" stroke="{TEAL}" stroke-width="3" opacity="0"/>',
           '<text class="t" x="550" y="222" text-anchor="middle" fill="#fff" font-size="21" font-weight="800">your vault</text>',
           f'<text class="t" x="550" y="246" text-anchor="middle" fill="{SOFT}" font-size="14">Markdown · [[links]]</text>',
           f'<text class="t" x="550" y="266" text-anchor="middle" fill="{SOFT}" font-size="14">lessons live in notes</text>',
           f'<g class="{tl.anim([(0, "opacity:0;transform:translateY(10px)"), (4.4, "opacity:0;transform:translateY(10px)", "ease-out"), (4.8, "opacity:1;transform:translateY(0px)"), (5.9, "opacity:1;transform:translateY(-6px)", "ease-in"), (6.4, "opacity:0;transform:translateY(-12px)"), (T, "opacity:0;transform:translateY(-12px)")])}" opacity="0">'
           f'<rect x="496" y="112" width="108" height="26" rx="13" fill="{TEAL}" fill-opacity=".14" stroke="{TEAL}" stroke-opacity=".8"/>'
           f'<text class="m" x="550" y="130" text-anchor="middle" fill="{TEAL}" font-size="13" font-weight="700">+1 lesson</text></g>']
    for k, (x, y, w, h, title, mono, line1, line2) in enumerate(cards):
        t0 = 2 * k
        glow = tl.window(t0, t0 + 1.25, 1, 0.3) if k else tl.anim(
            [(0, "opacity:1"), (0.95, "opacity:1", "ease-in"), (1.25, "opacity:0"), (9.7, "opacity:0", "ease-out"), (T, "opacity:1")])
        out.append(f'<g transform="translate({x} {y})">'
                   f'<rect class="{glow}" x="-3" y="-3" width="{w + 6}" height="{h + 6}" rx="18" fill="none" stroke="{TEAL}" '
                   f'stroke-width="6" opacity="0" filter="url(#lblur)"/>'
                   f'<rect class="card" width="{w}" height="{h}" rx="16"/>'
                   f'<rect class="{glow}" width="{w}" height="{h}" rx="16" fill="{TEAL}" fill-opacity=".05" stroke="{TEAL}" '
                   f'stroke-width="1.8" opacity="0"/>'
                   f'<circle class="dot" cx="26" cy="30" r="6" style="animation-delay:{k * .5}s"/>'
                   f'<text class="t h" x="44" y="37">{esc(title)}</text>')
        ty = 68
        if mono:
            out.append(f'<text class="m c" x="22" y="{ty}">{esc(mono)}</text>')
            ty += 22
        out.append(f'<text class="t s" x="22" y="{ty}">{esc(line1)}</text>')
        if line2:
            out.append(f'<text class="t s" x="22" y="{ty + 22}">{esc(line2)}</text>')
        if k == 4:  # a small curve that goes down while "it measures"
            out.append(f'<path class="{tl.anim([(0, "stroke-dashoffset:100"), (8.1, "stroke-dashoffset:100", "cubic-bezier(.3,.6,.3,1)"), (9.0, "stroke-dashoffset:0"), (9.7, "stroke-dashoffset:0", "linear"), (T, "stroke-dashoffset:100")])}" '
                       f'pathLength="100" d="M128,47 C142,49 146,57 158,58 C170,59 174,67 186,68 C194,69 200,72 208,73" fill="none" '
                       f'stroke="{TEAL}" stroke-width="2.5" stroke-linecap="round" style="stroke-dasharray:100"/>')
        out.append("</g>")
    for d in arrows:
        out.append(f'<path class="flow" d="{d}" marker-end="url(#larr)"/>')
    packets = []
    for k, d in enumerate(arrows):
        a, b = (2 * k + 1.05) / T, (2 * k + 2) / T
        packets.append(f'<g opacity="0"><circle r="11" fill="{TEAL}" opacity=".25"/><circle r="5" fill="#eafffa"/>'
                       f'<animateMotion dur="{T}s" repeatCount="indefinite" path="{d}" keyPoints="0;0;1;1" '
                       f'keyTimes="0;{a:.3f};{b:.3f};1" calcMode="spline" keySplines="0 0 1 1;.45 0 .25 1;0 0 1 1"/>'
                       f'<animate attributeName="opacity" dur="{T}s" repeatCount="indefinite" values="0;1;0" '
                       f'keyTimes="0;{a:.3f};{b:.3f}" calcMode="discrete"/></g>')
    out.append(f'<g class="fx">{"".join(packets)}</g>')
    style = "\n".join([
        f".t{{font-family:{FONT}}}.m{{font-family:{MONO}}}",
        f".card{{fill:{CARD};stroke:{LINE};stroke-width:1.5}}.h{{fill:#fff;font-size:19px;font-weight:700}}",
        f".s{{fill:{SOFT};font-size:13.5px}}.c{{fill:{TEAL};font-size:13px}}",
        f".flow{{fill:none;stroke:{PERI};stroke-width:2.2;stroke-dasharray:7 7;animation:dash 1.2s linear infinite}}",
        "@keyframes dash{to{stroke-dashoffset:-14}}",
        f".dot{{fill:{TEAL};animation:p 2.5s ease-in-out infinite}}@keyframes p{{0%,100%{{opacity:.35}}50%{{opacity:1}}}}",
        *tl.css, REDUCED])
    defs = (f'<defs><linearGradient id="la" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{TEAL}"/>'
            f'<stop offset=".5" stop-color="{PERI}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>'
            f'<marker id="larr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{PERI}"/></marker>'
            '<filter id="lblur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="7"/></filter>'
            '<pattern id="lgrid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" '
            'stroke="#ffffff" stroke-opacity=".03"/></pattern></defs>')
    return svg(1100, 470, "The learning loop: you work, it learns, you approve, the right rule fires at the right moment, "
               "it measures", style, defs + "\n" + "\n".join(out))


# --- stats ---------------------------------------------------------------------------------------

def stats() -> str:
    T = 10.0
    tl = Timeline(T, "s")
    vals = [12.4, 11.1, 9.8, 8.6, 7.1, 6.4, 5.8, 5.3]
    W, H, base, top, x0, bw, gap = 1100, 380, 300, 110, 72, 58, 30
    hmax = base - top - 12

    def mix(c1, c2, f):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))

    end = 9.3
    fade = lambda t: tl.anim([(0, "opacity:0"), (t, "opacity:0", "ease-out"), (t + .25, "opacity:1"),
                              (end, "opacity:1", "linear"), (end + .4, "opacity:0"), (T, "opacity:0")])
    out = [f'<rect width="{W}" height="{H}" rx="22" fill="#0b1020"/>', f'<rect width="{W}" height="{H}" rx="22" fill="url(#sgrid)"/>',
           f'<text class="t" x="{x0}" y="58" fill="#fff" font-size="22" font-weight="800">Mistakes per 100 messages</text>',
           f'<text class="t" x="{x0}" y="84" fill="{DIM}" font-size="14">example · your numbers come from your own sessions</text>',
           f'<line x1="{x0 - 12}" y1="{base}" x2="{x0 + 8 * (bw + gap) - gap + 12}" y2="{base}" stroke="#ffffff" stroke-opacity=".18"/>']
    for i, v in enumerate(vals):
        x = x0 + i * (bw + gap)
        h = hmax * v / max(vals)
        col = mix(PINK, TEAL, i / (len(vals) - 1))
        t = 0.5 + i * 0.32
        g = tl.anim([(0, "transform:scaleY(0)"), (t, "transform:scaleY(0)", "cubic-bezier(.2,.9,.25,1)"), (t + .6, "transform:scaleY(1)"),
                     (end, "transform:scaleY(1)", "ease-in"), (end + .4, "transform:scaleY(0)"), (T, "transform:scaleY(0)")],
                    "transform-box:fill-box;transform-origin:50% 100%")
        out.append(f'<rect class="{g}" x="{x}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="7" fill="{col}" fill-opacity=".9"/>')
        out.append(f'<text class="m {fade(t + .35)}" x="{x + bw / 2}" y="{base - h - 10:.1f}" fill="{col}" font-size="15" '
                   f'font-weight="700" text-anchor="middle">{v}</text>')
        out.append(f'<text class="m" x="{x + bw / 2}" y="{base + 24}" fill="{DIM}" font-size="13" text-anchor="middle">W{41 + i}</text>')
    # the drop, once the last bar stands
    bx, by = x + bw / 2, base - h - 62
    out.append(f'<g class="{tl.anim([(0, "opacity:0;transform:scale(.6)"), (3.4, "opacity:0;transform:scale(.6)", "cubic-bezier(.2,.9,.3,1.3)"), (3.9, "opacity:1;transform:scale(1)"), (end, "opacity:1;transform:scale(1)", "linear"), (end + .4, "opacity:0;transform:scale(1)"), (T, "opacity:0;transform:scale(1)")], "transform-box:fill-box;transform-origin:center")}">'
               f'<rect x="{bx - 46:.0f}" y="{by - 23:.0f}" width="92" height="34" rx="17" fill="{TEAL}" fill-opacity=".16" stroke="{TEAL}"/>'
               f'<text class="m" x="{bx:.0f}" y="{by:.0f}" fill="{TEAL}" font-size="17" font-weight="800" text-anchor="middle">−57 %</text></g>')
    # recall precision ring
    cx, cy, r = 940, 205, 62
    circ = 2 * 3.14159265 * r
    off = lambda p: circ * (1 - p)
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{LINE}" stroke-width="12"/>')
    out.append(f'<circle class="{tl.anim([(0, f"stroke-dashoffset:{off(.0):.1f}"), (0.6, f"stroke-dashoffset:{off(.0):.1f}", "cubic-bezier(.3,.7,.3,1)"), (1.6, f"stroke-dashoffset:{off(.41):.1f}", "cubic-bezier(.4,0,.2,1)"), (3.0, f"stroke-dashoffset:{off(.41):.1f}", "cubic-bezier(.4,0,.2,1)"), (4.6, f"stroke-dashoffset:{off(.64):.1f}"), (end, f"stroke-dashoffset:{off(.64):.1f}", "ease-in"), (end + .4, f"stroke-dashoffset:{off(0):.1f}"), (T, f"stroke-dashoffset:{off(0):.1f}")])}" '
               f'cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#sring)" stroke-width="12" stroke-linecap="round" '
               f'stroke-dasharray="{circ:.1f}" style="stroke-dashoffset:{off(.64):.1f}" transform="rotate(-90 {cx} {cy})"/>')
    for v, t0, t1 in (("41%", 1.2, 3.4), ("49%", 3.4, 3.8), ("57%", 3.8, 4.2)):
        out.append(f'<text class="m {tl.anim([(0, "opacity:0"), (t0, "opacity:1"), (t1, "opacity:0"), (T, "opacity:0")])}" x="{cx}" y="{cy + 9}" '
                   f'fill="#fff" font-size="27" font-weight="800" text-anchor="middle" style="opacity:0">{v}</text>')
    out.append(f'<text class="m {tl.anim([(0, "opacity:0"), (4.2, "opacity:1"), (end + .2, "opacity:0"), (T, "opacity:0")])}" x="{cx}" y="{cy + 9}" '
               f'fill="#fff" font-size="27" font-weight="800" text-anchor="middle">64%</text>')
    out.append(f'<text class="t" x="{cx}" y="{cy + r + 38}" fill="#fff" font-size="16" font-weight="700" text-anchor="middle">recall precision</text>')
    out.append(f'<text class="t" x="{cx}" y="{cy + r + 60}" fill="{DIM}" font-size="13.5" text-anchor="middle">tunes itself from what your agent opens</text>')
    style = "\n".join([f".t{{font-family:{FONT}}}.m{{font-family:{MONO}}}", *tl.css, REDUCED])
    defs = (f'<defs><linearGradient id="sring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PERI}"/>'
            f'<stop offset="1" stop-color="{TEAL}"/></linearGradient>'
            '<pattern id="sgrid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" '
            'stroke="#ffffff" stroke-opacity=".03"/></pattern></defs>')
    return svg(W, H, "Example: mistakes per 100 messages fall from 12.4 to 5.3 over eight weeks (−57 %); "
               "recall precision rises from 41 % to 64 %", style, defs + "\n" + "\n".join(out))


if __name__ == "__main__":
    for name, fn in (("banner", banner), ("demo", demo), ("setup", setup_demo), ("loop", loop), ("stats", stats)):
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print(f"wrote docs/assets/{name}.svg  ({len((OUT / f'{name}.svg').read_bytes()) // 1024} KB)")
