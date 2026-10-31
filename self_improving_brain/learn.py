"""The learning loop: your corrections -> rules your agent follows -> measured, sharpened, retired.

The model itself never learns. What gets better is *what it is told, when*.

  scan    reads new session transcripts (no LLM) and keeps every moment you corrected the agent, rejected
          a tool call or said how things should be done, as a draft
  judge   the nightly LLM sorts drafts: a lasting rule, steering for one task, a fact to remember, or noise
  adopt   no LLM, just policy: a rule you gave twice (in two sessions) or stated as lasting ("always",
          "never", "from now on") becomes a lesson in the note that owns the topic. A rule that comes back
          although it exists is sharpened, then escalated. Every change lands in the journal, undoable.

Only your own words become rules. What an agent reports about itself (MCP `brain_correction`) waits for you.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from . import events, journal, ledger, llm, trust
from .config import Vault
from .notes import MECHANISMS, Lesson, Library, atomic_write, dump_frontmatter, set_field
from .transcripts import parse
from .util import (append_jsonl, days_since, digest, iso_week, load_json, now_iso, read_jsonl, save_json, short,
                   slugify, terms, today)

# --- noticing a correction, in six languages (en de es fr pt it) ---------------------------------------------
# Tuned on the dev half of eval/corrections.jsonl, measured on the other half: `python3 eval/run.py`.
# A bare "no" at the start is most often an *answer* to a question the agent asked, so it only counts when
# the agent did not just ask one. A user correcting their own instruction, a "why" asked out of curiosity and
# a "you're right, works now" never count.

def _rx(*parts: str, start: bool = False) -> re.Pattern:
    return re.compile((r"^\s*(?:" if start else r"(?<!\w)(?:") + "|".join(parts) + r")(?!\w)", re.I)


STRONG = _rx(
    r"why (did|would|do) you", r"i told you", r"i (already )?said", r"you should(n'?t)? have", r"you were supposed to",
    r"not what i (asked|said|meant|wanted)", r"that'?s not what", r"did i ask", r"i didn'?t ask", r"how many times",
    r"stop doing", r"don'?t ever", r"never do that", r"you did it again", r"same mistake", r"again\?!",
    r"did you (actually|even|really) (run|test|try|check|read|look)", r"(second|third|fourth|fifth) time",
    r"(warum|wieso|weshalb) hast du", r"ich hatte (dir )?(doch )?gesagt", r"ich sagte doch", r"hab(e)? ich (dir )?doch gesagt",
    r"hab(e)? ich nicht gesagt", r"hab(e)? ich nicht gefragt", r"nicht schon wieder", r"das (war|ist) falsch",
    r"falsch verstanden", r"wie oft noch", r"du kannst doch nicht", r"zum (zweiten|dritten|x-ten) mal",
    r"por qu[eé] (has|hiciste|me has|lo has|la has|los has|las has)", r"(ya )?te (lo )?(dije|hab[ií]a dicho|he dicho)",
    r"no es lo que (te )?(ped[ií]|dije|quer[ií]a)", r"(yo )?(solo )?te ped[ií]", r"no te ped[ií]", r"cu[aá]ntas veces",
    r"ya van (dos|tres|cuatro|\d+) veces",
    r"pourquoi (t'as|tu as|as-tu|t'es|tu es)", r"je t'(avais|ai) (dit|demand[ée])", r"je te l'(avais|ai) dit",
    r"combien de fois", r"encore une fois", r"c'est pas ce que", r"ce n'est pas ce que", r"je (ne )?t'ai (pas|jamais) demand[ée]",
    r"por ?qu[eê] (voc[eê] )?(fez|apagou|mudou|rodou|colocou|tirou|removeu|deletou|mexeu)", r"eu te (disse|falei|pedi)",
    r"eu (disse|falei|pedi) (pra|para|que)", r"n[aã]o era isso", r"n[aã]o foi isso que", r"quantas vezes", r"t[aá] maluco",
    r"perch[eé] (hai|avete)", r"ti (avevo|ho) (detto|chiesto)", r"quante volte", r"ancora una volta", r"non [eè] quello che",
    r"non ti ho chiesto")
MEDIUM = _rx(
    r"that'?s wrong", r"that is wrong", r"incorrect", r"doesn'?t work", r"does not work",
    r"still (broken|failing|fails|fail|wrong|missing|not working)", r"you forgot", r"you didn'?t", r"you missed",
    r"you broke", r"no,? you", r"not optional", r"please (don'?t|do not|not|never|stop)",
    r"du sollst", r"du solltest", r"schon wieder", r"hast du (vergessen|nicht)", r"geht (immer )?noch nicht", r"geht nicht",
    r"funktioniert (immer )?(noch )?nicht", r"klappt nicht", r"(nein|ne),? du", r"stimmt nicht", r"immer noch (nicht|falsch|kaputt|rot)",
    r"sigue(n)? (sin|fallando|saliendo|mal|roto|igual|apareciendo)", r"no funciona", r"se te (olvid[oó]|pas[oó])",
    r"te olvidaste", r"(eso|esto) (no es|est[aá] mal)", r"otra vez", r"otras veces", r"de nuevo",
    r"(ça|ca|il|elle) (ne )?(marche|fonctionne) (toujours )?pas", r"toujours pas", r"t'as (oubli[ée]|pas)", r"tu as oubli[ée]",
    r"tu n'as pas", r"c'est faux", r"(à|a) nouveau", r"de nouveau",
    r"continua (faltando|dando|quebrado|errado|com|sem)", r"ainda (n[aã]o funciona|est[aá] errado|t[aá] errado|quebrado|faltando)",
    r"n[aã]o funciona", r"voc[eê] esqueceu", r"(isso|isto) (est[aá]|t[aá]) errado", r"de novo", r"outra vez", r"mais uma vez",
    r"ancora (tutto )?(sballato|rotto|sbagliato|non funziona)", r"non funziona", r"hai dimenticato", r"ti sei dimenticat[oa]",
    r"(questo|quello|cos[iì]) [eè] sbagliato", r"di nuovo", r"un'altra volta")
# "undo that", as the first words
UNDO = _rx(r"(undo|revert|roll back) (that|this|it)", r"put it back", r"mach das r[uü]ckg[aä]ngig", r"r[uü]ckg[aä]ngig",
           r"desh[aá]z(lo)?", r"revi[eé]rte(lo)?", r"annule(-le)?", r"remets", r"desfa[zç]a?", r"annulla", r"rimetti", start=True)
# Openers that are as often an *answer* to the agent's question as a correction ("No, that's fine").
START = re.compile(
    r"^\s*(no+|nope|non|n[aã]o|wrong|stop|wait|huh|nein|ne+|nö|falsch|stopp?|halt|hä+|moment|warte|arr[eê]te|attends|"
    r"espera|pera|aspetta|fermo|basta)(?:[\s,.!?:]|$)|^\s*para\s*[.!]*\s*$|"
    r"(?<!\w)(no,? that|nein,? das|ne,? das|yes,? but|ja,? aber|s[ií],? pero|oui,? mais|sim,? mas|s[iì],? ma|actually,)(?!\w)",
    re.I)
# The user corrects their own instruction, asks out of curiosity, or agrees: never a correction of the agent.
NOT_THE_AGENT = _rx(
    r"my (bad|mistake|fault)", r"i meant", r"i misspoke", r"i was wrong", r"i got (it|that) wrong", r"i'?m in the wrong",
    r"i had the wrong", r"you'?re right", r"works now", r"just (curious|wondering|asking)", r"out of curiosity",
    r"not a criticism",
    r"mein fehler", r"ich hab(e)? mich (vertan|verschrieben|geirrt)", r"ich meinte", r"du hast recht", r"funktioniert jetzt",
    r"nur (interessehalber|aus neugier)", r"keine kritik",
    r"me (equivoqu[eé]|expliqu[eé] mal)", r"quise decir", r"culpa m[ií]a", r"error m[ií]o", r"tienes raz[oó]n", r"ya funciona",
    r"por curiosidad", r"no es (una )?cr[ií]tica",
    r"je me suis tromp[ée]e?", r"je voulais dire", r"j'ai dit n'importe quoi", r"c'est (de )?ma faute", r"mon erreur",
    r"tu as raison", r"je demande (juste )?pour comprendre", r"(c'est )?pas une critique", r"par curiosit[ée]",
    r"falei errado", r"me expressei (mal|errado)", r"eu quis dizer", r"foi mal", r"culpa minha", r"erro meu",
    r"voc[eê] tem raz[aã]o", r"funcionou", r"s[oó] (por )?curiosidade", r"n[aã]o [eé] cr[ií]tica",
    r"ho sbagliato io", r"intendevo", r"colpa mia", r"mi sono (sbagliat[oa]|spiegat[oa] male)", r"errore mio",
    r"hai ragione", r"(ora|adesso) funziona", r"per curiosit[aà]", r"non [eè] una critica")
# A lasting rule by its own words: "from now on", "in general", "next time".
FUTURE = _rx(
    r"from now on", r"going forward", r"in (the )?future", r"next time", r"in general", r"as a rule", r"house rule",
    r"ab jetzt", r"ab sofort", r"in zukunft", r"künftig", r"kuenftig", r"grundsätzlich", r"generell", r"nächstes mal",
    r"de ahora en adelante", r"a partir de ahora", r"en (el )?futuro", r"la pr[oó]xima vez", r"para (las? )?(pr[oó]xim[ao]s?|futur[ao]s?)",
    r"por norma", r"en general",
    r"(à|a) partir de maintenant", r"d[ée]sormais", r"dor[ée]navant", r"(à|a) l'avenir", r"la prochaine fois",
    r"pour les prochaines fois", r"en g[ée]n[ée]ral", r"pour info", r"info importante",
    r"daqui (pra|para) frente", r"a partir de agora", r"no futuro", r"da pr[oó]xima vez", r"regra da casa", r"em geral",
    r"d'ora in (poi|avanti)", r"da (ora|adesso) in poi", r"in futuro", r"la prossima volta", r"in generale", r"di regola")
REMEMBER = _rx(r"remember (that|this|to)", r"keep in mind", r"always make sure", r"never again", r"merk dir", r"merke dir",
               r"denk dran", r"recuerda", r"ten en cuenta", r"retiens", r"n'oublie pas", r"lembr[ae](-se)?", r"ricorda(ti)?")
LASTING = _rx(
    r"always", r"never", r"every time", r"each time", r"by default",
    r"immer", r"nie", r"niemals", r"jedes mal", r"standardmäßig",
    r"siempre", r"nunca", r"jam[aá]s", r"cada vez", r"por defecto",
    r"toujours", r"jamais", r"chaque fois", r"par d[ée]faut",
    r"sempre", r"toda vez", r"por padr[aã]o",
    r"mai", r"ogni volta", r"di default")
NOT_LASTING = _rx(r"never ?mind", r"as always", r"wie immer", r"como siempre", r"comme toujours", r"como sempre",
                  r"come sempre", r"come mai", r"(war|ist) schon immer", r"da sempre", r"desde siempre", r"depuis toujours",
                  r"desde sempre", r"(has|have) always been", r"always been")
# How things are done here: "in this repo", "bei uns", "aquí se", "regra da casa".
HOUSE = _rx(r"in this (repo|project|codebase|team|company)", r"here we", r"we (always|never|don'?t)", r"our (convention|rule)",
            r"bei uns", r"machen wir das", r"in (diesem|unserem) (repo|projekt)",
            r"aqu[ií] (se|siempre|nunca|usamos)", r"en este (repo|proyecto)",
            r"ici,? (on|les|nous)", r"chez nous", r"dans ce (repo|projet)",
            r"aqui (a gente|sempre|nunca|usamos)", r"neste (repo|projeto)", r"a gente (sempre|nunca)",
            r"da noi", r"in questo (repo|progetto)")
EVERY = _rx(r"every", r"each", r"jede[rsnm]?", r"cada", r"tod[oa]s? (as?|os?|las?|los?)", r"toda", r"chaque", r"tou(s|tes) les",
            r"ogni", r"tutt[ie] (i|le|gli)")
MUST = _rx(r"must", r"should", r"needs?", r"has to", r"have to", r"muss", r"m[uü]ssen", r"soll(en)?", r"braucht",
           r"debe(n)?", r"tiene(n)? que", r"hay que", r"necesita", r"doi(t|vent)", r"faut", r"deve(m)?", r"precisa(m)?",
           r"tem que", r"devono", r"bisogna")
# A bug report says "always" too ("the export always fails since Monday"): not a rule.
BUGGY = _rx(r"error", r"fail(s|ed|ing)?", r"bug", r"broken", r"crash\w*", r"timeout", r"exception", r"traceback",
            r"fehler", r"kaputt", r"falla", r"roto", r"erreur", r"cass[ée]e?", r"plante", r"erro", r"quebr\w*", r"errore",
            r"rott[oa]")
ACCIDENT = re.compile(r"^\s*(sorry|sry|sorri|oops|ups|weiter|continue|go on|mach weiter|ok(ay)?|passt|egal|"
                      r"aus versehen|versehentlich|my bad|mistake)\b", re.I)
DRAFT_MIN = 0.4
EXPIRE_DAYS = 120   # a correction that never came back in four months was not a pattern
# a guard's command regex must not stop everyday work
HARMLESS = ("git status", "git diff", "git log --oneline", "ls -la", "cat README.md", "npm test", "pytest -q",
            "python3 -m unittest", "grep -rn foo .", "cd src")


def correction_score(text: str, agent_asked: bool) -> float:
    head = text[:220]
    if NOT_THE_AGENT.search(text[:400]):
        return 0.0
    if STRONG.search(head):
        return 0.85
    if MEDIUM.search(head) or UNDO.search(head):
        return 0.55
    if START.search(head):
        if agent_asked or head.rstrip().endswith("?"):
            return 0.15  # an answer to the agent's question, not a correction
        return 0.45
    return 0.0


def house_rule(text: str) -> bool:
    """How things should be done, said as a rule: "in this repo …", "… always …", "every migration needs …"."""
    head = text[:400]
    if head.rstrip().endswith("?") or BUGGY.search(head) or NOT_THE_AGENT.search(head):
        return False
    if HOUSE.search(head):
        return True
    if LASTING.search(head) and not NOT_LASTING.search(head):
        return True
    return bool(EVERY.search(head) and MUST.search(head))


def _agent_asked(turns, i: int) -> bool:
    for t in reversed(turns[:i]):
        if t.role == "assistant":
            return "?" in t.text[-400:]
        if t.role == "user":
            return False
    return False


def classify(turns, i: int):
    """(kind, score, quote) for turn i, or None. kind: lesson | preference."""
    t = turns[i]
    if t.role == "user":
        if len(t.text) > 1500 or NOT_THE_AGENT.search(t.text[:400]):
            return None
        head = t.text[:400]
        if FUTURE.search(head):
            return ("preference", 0.8, t.text)
        if REMEMBER.search(head):
            return ("preference", 0.8 if LASTING.search(head) else 0.35, t.text)
        sc = correction_score(t.text, _agent_asked(turns, i))
        if sc < 0.55 and house_rule(t.text):
            return ("preference", 0.5, t.text)
        return ("lesson", sc, t.text) if sc else None
    if t.role == "tool_result" and t.rejected:
        nxt = next((x for x in turns[i + 1:i + 4] if x.role == "user"), None)
        if nxt is None:
            return ("lesson", 0.4, "(tool call rejected)")
        if ACCIDENT.search(nxt.text[:60]):
            return None
        sc = correction_score(nxt.text, False)
        if sc >= 0.45:
            return ("lesson", max(0.75, sc), nxt.text)
        return ("lesson", 0.42 if len(nxt.text.split()) >= 4 else 0.3, nxt.text)
    return None


def _score(d: dict) -> float:
    try:
        return float(d.get("score", 0.5))
    except (TypeError, ValueError):
        return 0.5


def _overlap(ta: set, tb: set) -> float:
    m = min(len(ta), len(tb))
    return len(ta & tb) / m if m >= 3 else 0.0


def similarity(a: str, b: str) -> float:
    return _overlap(set(terms(a)), set(terms(b)))


def groups(ds: list[dict], threshold: float = 0.5, limit: int = 500) -> list[list[dict]]:
    """Cluster similar drafts. A mistake you corrected four times beats one you corrected once.
    Only the `limit` strongest drafts are clustered, so a long history never slows anything down."""
    out: list[tuple[set, list[dict]]] = []
    for d in sorted(ds, key=lambda x: -_score(x))[:limit]:
        t = set(terms(d.get("rule") or d.get("quote", "")))
        for key, g in out:
            if _overlap(t, key) >= threshold:
                g.append(d)
                break
        else:
            out.append((t, [d]))
    gs = [g for _, g in out]
    gs.sort(key=lambda g: -(max(_score(x) for x in g) + 0.15 * (len(g) - 1)))
    return gs


# a draft only moves forward: what another machine decided later wins over an older copy
RANK = {"open": 0, "held": 1, "once": 2, "noise": 2, "expired": 2, "approved": 3, "rejected": 3}


def drafts_path(v: Vault) -> Path:
    """This machine's drafts (one file per machine, see ledger.py)."""
    return ledger.own(v, "drafts")


def all_drafts(v: Vault) -> list[dict]:
    return ledger.load(v, "drafts", RANK)


def save_drafts(v: Vault, records: list[dict]) -> None:
    ledger.save(v, "drafts", records, RANK)


def drafts(v: Vault, status: str | None = "open") -> list[dict]:
    return [x for x in all_drafts(v) if status is None or x.get("status") == status]


def rejected_topics(v: Vault) -> list[dict]:
    return [t for f in ledger.files(v, "rejected") for t in read_jsonl(f)]


def _blocked(text: str, topics: list[dict]) -> bool:
    t = set(terms(text))
    for top in topics:
        words = [w.lower() for w in top.get("words", [])]
        if sum(1 for w in words if w in t) >= min(2, len(words) or 1):
            return True
    return False


def _context_before(turns, i: int) -> str:
    """The agent action the user reacted to: last assistant text or tool call before turn i."""
    for t in reversed(turns[:i]):
        if t.role == "tool_call":
            return f"[{t.tool}] {short(t.text, 220)}"
        if t.role == "assistant":
            return short(t.text, 260)
    return ""


def transcript_files(v: Vault) -> list[Path]:
    out = []
    for pattern in v.get("transcripts") or []:
        out.extend(Path(p) for p in glob.glob(os.path.expanduser(pattern), recursive=True))
    return sorted(set(out), key=lambda p: p.stat().st_mtime if p.exists() else 0)


def scan(v: Vault, files: list[Path] | None = None, max_age_days: int = 21) -> dict:
    """Mine transcripts not seen before. Each file is read once (signature: mtime + size). No LLM."""
    seen = load_json(v.path("state", "learn_seen.json"), {"files": {}, "items": []})
    seen_items = set(seen.get("items", []))
    topics = rejected_topics(v)
    shown_by_session: dict[str, set] = {}
    for e in events.all_events(v, "lesson"):
        shown_by_session.setdefault(e.get("session", ""), set()).add(e.get("id"))

    import time
    cutoff = time.time() - max_age_days * 86400
    stats = {"transcripts": 0, "user_messages": 0, "corrections": 0, "new_drafts": 0}
    new: list[dict] = []
    for f in files if files is not None else transcript_files(v):
        try:
            st = f.stat()
        except OSError:
            continue
        sig = f"{st.st_mtime:.0f}:{st.st_size}"
        if files is None and (st.st_mtime < cutoff or seen["files"].get(str(f)) == sig):
            continue
        tr = parse(f)
        seen["files"][str(f)] = sig
        if tr is None or not tr.turns:
            continue
        if tr.internal:
            continue  # our own headless LLM calls must never be mined as "user corrections"
        stats["transcripts"] += 1
        n_user = n_corr = 0
        counted: set[int] = set()
        for i, t in enumerate(tr.turns):
            if t.role == "user":
                n_user += 1
            c = classify(tr.turns, i)
            if not c or c[1] < DRAFT_MIN:
                continue
            kind, score, quote = c
            if kind == "lesson":
                # a rejection and the user's explanation right after it are one correction
                j = next((k for k in range(i, min(i + 4, len(tr.turns))) if tr.turns[k].role == "user"), i)
                if j in counted or i in counted:
                    continue
                counted.update({i, j})
                n_corr += 1
            key = digest(str(tr.session), quote[:300], str(t.ts or ""))
            if key in seen_items:
                continue
            seen_items.add(key)
            week = iso_week(t.ts or today())
            if kind == "lesson":
                events.log(v, "correction", session=tr.session, week=week, ts=t.ts, text=short(quote, 300),
                           draft="d-" + key[:8], lessons_shown=sorted(shown_by_session.get(tr.session, set())))
            if _blocked(quote, topics):
                continue
            new.append({"id": "d-" + key[:8], "t": now_iso(), "kind": kind, "status": "open", "score": score,
                        "quote": short(quote, 500), "context": _context_before(tr.turns, i),
                        "session": tr.session, "source": tr.source, "ts": t.ts, "rule": "",
                        **({"rejected": True} if t.role == "tool_result" else {})})
        stats["user_messages"] += n_user
        stats["corrections"] += n_corr
        events.log(v, "turns", session=tr.session, source=tr.source, user=n_user, corrections=n_corr,
                   tokens=tr.tokens, week=iso_week((tr.turns[0].ts or today())))
    for d in new:
        append_jsonl(drafts_path(v), d)
    stats["new_drafts"] = len(new)
    seen["items"] = sorted(seen_items)[-20000:]
    save_json(v.path("state", "learn_seen.json"), seen)
    return stats


# --- judge: the nightly LLM sorts what you said --------------------------------------------------

JUDGE_PROMPT = llm.MARKER + """
You curate the rules a coding agent follows for one user. Below are groups of moments from the user's real
sessions where they corrected the agent, rejected a tool call, or said how things should be done. Similar
moments are grouped; "sessions" says in how many different sessions it happened.

For EACH group decide:
- verdict: "rule"  a way of working worth keeping: a mistake likely to repeat, a lasting preference, a convention
           "fact"  the user asked to remember a fact (a port, a name, where something lives), not a way of working
           "once"  steering of this one task only (which file, which value, "not now", fixing a typo)
           "noise" not a correction: an answer to the agent's question, praise, a new task, the user correcting
                   themselves, a bug report about the code
- explicit: true only if the user's own words make it lasting ("always", "never", "from now on", "in this repo we",
  in any language)
- rule: for "rule" ONE imperative sentence (for "fact" one plain sentence), max 200 chars, concrete enough to act
  on, in the language the user wrote in. It must follow from the user's words, never from the agent's.
- mechanism: "reflex" (shown when its words, tools or commands come up; the default), "always" (needed in every
  session, e.g. how to answer the user), "guard" (only for an irreversible action the user stopped: ask first)
- words: 2-5 short phrases the user types when the rule matters. tools: tool names if the rule is about one tool.
  commands: a Python regex on the shell command if the rule is about a command, else "".
- class: a short kebab-case mistake class, e.g. "unverified-claim", "scope-creep", "skipped-tests".
- existing: the id of an existing rule below that covers the same thing, else "". If the user corrected it although
  that rule existed, "rule" is a sharper version of it.

Existing rules:
@RULES@

Return a JSON list: [{"group": 1, "verdict": "...", "explicit": false, "rule": "...", "mechanism": "...",
"words": [], "tools": [], "commands": "", "class": "", "existing": ""}]

Groups:
@GROUPS@
"""


def _candidates(ds: list[dict]) -> list[dict]:
    """Drafts the nightly run may act on: from a transcript (your own words), not from an agent's report."""
    return [d for d in ds if d.get("source") != "mcp" and d.get("status") in ("open", "held")]


def _render_group(n: int, g: list[dict]) -> str:
    d = g[0]
    sessions = len({x.get("session") for x in g})
    lines = [f"[{n}] sessions: {sessions} · {d.get('kind', 'lesson')}"
             + (" · the user rejected the tool call" if any(x.get("rejected") for x in g) else "")]
    if d.get("context"):
        lines.append(f"    agent did: {short(d['context'], 220)}")
    lines.append(f"    user said: {short(d.get('quote', ''), 400)}")
    for x in g[1:3]:
        lines.append(f"    also said: {short(x.get('quote', ''), 200)}")
    return "\n".join(lines)


def judge(v: Vault, batch: int = 20, batches: int = 3) -> int:
    """Send groups that hold new drafts to the LLM; store its verdict on each new draft. Returns groups judged."""
    total = 0
    for _ in range(batches):
        all_d = all_drafts(v)
        mine = {x.get("id") for x in read_jsonl(drafts_path(v))}  # other machines judge their own drafts
        todo = [g for g in groups(_candidates(all_d))
                if any(x.get("status") == "open" and x.get("id") in mine for x in g)][:batch]
        if not todo:
            break
        rules = "\n".join(f"- {l.id}: {l.rule}" for l in Library(v.root).lessons()) or "(none yet)"
        body = "\n\n".join(_render_group(i, g) for i, g in enumerate(todo, 1))
        try:
            res = llm.ask_json(v, JUDGE_PROMPT.replace("@RULES@", rules).replace("@GROUPS@", body))
        except (llm.NoLLM, ValueError, OSError) as e:
            events.log(v, "error", where="judge", msg=str(e)[:200])
            break
        if isinstance(res, dict):
            res = res.get("groups") or res.get("items") or [res]
        by_n: dict[int, dict] = {}
        for r in res if isinstance(res, list) else []:
            try:
                by_n[int(r.get("group"))] = r
            except (TypeError, ValueError, AttributeError):
                continue
        verdicts: dict[str, dict] = {}
        for i, g in enumerate(todo, 1):
            for x in g:
                if x.get("status") == "open" and x.get("id") in mine:
                    verdicts[x["id"]] = by_n.get(i) or {}
        # the LLM call takes a while: update the file as it is *now*, so drafts appended meanwhile survive
        fresh = all_drafts(v)
        for d in fresh:
            if d.get("id") not in verdicts or d.get("status") != "open":
                continue
            r = verdicts[d["id"]]
            verdict = str(r.get("verdict") or "").lower()
            if verdict not in ("rule", "fact", "once", "noise"):
                d["tries"] = int(d.get("tries") or 0) + 1  # skipped by the LLM: once more next night, then let go
                if d["tries"] >= 2:
                    d["status"] = "noise"
                continue
            d["verdict"] = verdict
            d["explicit"] = bool(r.get("explicit"))
            if verdict in ("once", "noise"):
                d["status"] = verdict
                continue
            d["status"] = "held"
            d["rule"] = short(str(r.get("rule") or ""), 220)
            d["existing"] = str(r.get("existing") or "")
            d["suggest"] = {k: r.get(k) for k in ("mechanism", "words", "tools", "commands", "class")}
        save_drafts(v, fresh)
        total += len(todo)
    return total


# --- adopt: policy, no LLM -------------------------------------------------------------------------

def _regex_ok(rx: str) -> bool:
    try:
        re.compile(rx)
    except re.error:
        return False
    return True


def _guard_regex(rx: str) -> str:
    """A command regex fit for a guard: valid, and silent on everyday commands."""
    rx = str(rx or "").strip()
    if not rx or len(rx) > 300 or not _regex_ok(rx):
        return ""
    return "" if any(re.search(rx, c) for c in HARMLESS) else rx


def _suspicious(text: str) -> bool:
    """Never adopt on its own what carries a link or a secret: that is what an injected instruction looks like."""
    from .secrets import scan as secret_scan
    return bool(re.search(r"https?://|www\.", text, re.I) or secret_scan(text))


def _place(v: Vault, lib: Library, text: str, note: str = ""):
    """The note that owns a topic: the one you named, else the best search hit, else working-style."""
    target = lib.find(note) if note else None
    if note and target is None:
        raise KeyError(f"note {note!r} not found")
    if target is None:
        from .search import Index
        hits = [h for h in Index(lib).score(text, include_hubs=False)
                if h.score >= 2 * float(v.get("recall_threshold"))]
        target = hits[0].note if hits else None
    return (target.path, target.name) if target else (default_note(v), "working-style")


def _evidence(g: list[dict]) -> str:
    quote = next((d.get("quote") for d in g if d.get("quote") and not d["quote"].startswith("(")), "") or ""
    return short(quote, 140).replace("\n", " ")


def _why(g: list[dict], explicit: bool) -> str:
    sessions = len({d.get("session") for d in g})
    q = _evidence(g)
    if sessions >= 2:
        return f"you corrected this in {sessions} sessions" + (f": “{q}”" if q else "")
    return ("you said it should always be so" if explicit else "you asked for it") + (f": “{q}”" if q else "")


def _new_lesson(v: Vault, lib: Library, g: list[dict], best: dict, always_n: int) -> Lesson:
    sug = best.get("suggest") or {}
    rule = best["rule"]
    mech = str(sug.get("mechanism") or "reflex").lower()
    mech = mech if mech in MECHANISMS else "reflex"
    tools = [str(t) for t in (sug.get("tools") or []) if str(t).strip()][:4]
    commands = str(sug.get("commands") or "")
    commands = commands if _regex_ok(commands) and len(commands) <= 300 else ""
    if mech == "guard":
        # the brain only asks where you once said no: a guard needs a rejected tool call and a precise trigger
        commands = _guard_regex(commands)
        if not any(d.get("rejected") for d in g) or not (commands or tools):
            mech = "reflex"
    if mech == "always" and always_n >= int(v.get("always_max")):
        mech = "reflex"
    words = [str(w).strip() for w in (sug.get("words") or []) if str(w).strip()][:5] or terms(rule)[:4]
    path, name = _place(v, lib, rule + " " + best.get("quote", ""))
    klass = str(sug.get("class") or "")
    lesson = Lesson(id=unique_lesson_id(lib, rule, klass), note=name, rule=rule, mechanism=mech, tools=tools,
                    commands=commands, words=words, since=today(), evidence=_evidence(g), klass=klass)
    write_lesson(v, path, lesson)
    trust.record(v, [lesson], "learned")
    return lesson


def _similar_lesson(rule: str, active: dict) -> Lesson | None:
    best, top = None, 0.0
    for l in active.values():
        s = similarity(rule, l.rule + " " + " ".join(l.words))
        if s > top:
            best, top = l, s
    return best if top >= 0.6 else None


def _relapse(v: Vault, l: Lesson, g: list[dict], best: dict, always_n: int) -> dict | None:
    """The same mistake came back although the rule existed: first sharpen it, then escalate it."""
    after = [d for d in g if (d.get("ts") or d.get("t") or "")[:10] > (l.since or "")]
    if not after:
        return None  # older evidence for a rule you already have
    q = _evidence(after)
    if journal.count(v, l.id, ("sharpen", "mechanism")) == 0 or l.mechanism in ("always", "guard"):
        new = best.get("rule") or ""
        if not new or new == l.rule or _suspicious(new):
            return None
        sug = best.get("suggest") or {}
        words = list(dict.fromkeys(l.words + [str(w).strip() for w in (sug.get("words") or []) if str(w).strip()]))[:8]
        set_lesson_field(v, l.id, "rule", new, by="sharpened")
        if words != l.words:
            set_lesson_field(v, l.id, "words", ", ".join(words), by="sharpened")
        return journal.log(v, "sharpen", lesson=l.id, note=l.note, before=l.rule, after=new,
                           why="the mistake came back although the rule existed" + (f": “{q}”" if q else ""))
    sug = best.get("suggest") or {}
    commands = l.commands or _guard_regex(str(sug.get("commands") or ""))
    if (l.tools or commands) and any(d.get("rejected") for d in after):
        to = "guard"
    elif always_n < int(v.get("always_max")):
        to = "always"
    else:
        return None
    if to == "guard" and commands and not l.commands:
        set_lesson_field(v, l.id, "commands", commands, by="escalated")
    set_lesson_field(v, l.id, "mechanism", to, by="escalated")
    return journal.log(v, "mechanism", lesson=l.id, note=l.note, rule=l.rule, before=l.mechanism, after=to,
                       why="it came back after it was sharpened" + (f": “{q}”" if q else ""))


def adopt(v: Vault) -> dict:
    """Judged drafts become lessons (or facts in the inbox) by fixed rules. Returns what changed."""
    from . import inbox
    all_d = all_drafts(v)
    for d in all_d:  # what never came back in months was not a pattern
        if d.get("status") in ("open", "held", "once") and (days_since(str(d.get("t", ""))[:10]) or 0) > EXPIRE_DAYS:
            d["status"] = "expired"
    topics = rejected_topics(v)
    lib = Library(v.root)
    active = {l.id: l for l in lib.lessons()}
    always_n = sum(1 for l in active.values() if l.mechanism == "always")
    out = {"rules": [], "sharpened": [], "escalated": [], "facts": 0, "waiting": 0}
    cap = int(v.get("learn_max_new"))
    held = [d for d in _candidates(all_d) if d.get("status") == "held"]
    for g in groups(held):
        best = max(g, key=lambda d: (str(d.get("t", "")), _score(d)))
        rule = str(best.get("rule") or "")
        if not rule:
            continue
        if _blocked(rule + " " + best.get("quote", ""), topics):
            for d in g:
                d["status"] = "rejected"  # a topic you undid once is not learned again
            continue
        if best.get("verdict") == "fact":
            if not _suspicious(rule):
                try:
                    inbox.add(v, "fact", rule, quote=best.get("quote", ""), source="learn", session=best.get("session", ""))
                    out["facts"] += 1
                except inbox.Rejected:
                    pass
            for d in g:
                d["status"] = "approved"
            continue
        ex = active.get(str(best.get("existing") or "")) or _similar_lesson(rule, active)
        if ex is not None:
            j = _relapse(v, ex, g, best, always_n)
            if j:
                out["sharpened" if j["kind"] == "sharpen" else "escalated"].append(ex.id)
                if j["kind"] == "mechanism" and j["after"] == "always":
                    always_n += 1
                lib = Library(v.root)
                active = {l.id: l for l in lib.lessons()}
            for d in g:
                d["status"], d["lesson"] = "approved", ex.id
            continue
        sessions = len({d.get("session") for d in g})
        explicit = any(d.get("explicit") or (d.get("kind") == "preference" and _score(d) >= 0.8) for d in g)
        if not (sessions >= 2 or explicit) or _suspicious(rule) or len(out["rules"]) >= cap:
            out["waiting"] += 1  # once is not a pattern yet; it is adopted when it comes back
            continue
        lesson = _new_lesson(v, lib, g, best, always_n)
        always_n += lesson.mechanism == "always"
        journal.log(v, "rule", lesson=lesson.id, note=lesson.note, rule=lesson.rule, mechanism=lesson.mechanism,
                    why=_why(g, explicit), drafts=[d["id"] for d in g])
        out["rules"].append(lesson.id)
        lib = Library(v.root)
        active = {l.id: l for l in lib.lessons()}
        for d in g:
            d["status"], d["lesson"] = "approved", lesson.id
    save_drafts(v, all_d)
    return out


# --- lessons in notes ------------------------------------------------------------------------------

def unique_lesson_id(lib: Library, rule: str, klass: str = "") -> str:
    base = "L-" + (slugify(klass) + "-" if klass else "") + slugify(" ".join(terms(rule)[:4]))
    existing = {l.id for l in lib.lessons(retired=True)}
    lid, i = base, 2
    while lid in existing:
        lid, i = f"{base}-{i}", i + 1
    return lid


def default_note(v: Vault) -> Path:
    p = v.root / "20-areas" / "working-style.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump_frontmatter({"name": "working-style", "description":
                     "How I want my agents to work: lessons that apply across projects.",
                     "type": "preference", "updated": today()}) +
                     "\n# Working style\n\nRules learned from real sessions. Linked from [[hub-brain]].\n",
                     encoding="utf-8")
    return p


def write_lesson(v: Vault, note_path: Path, lesson: Lesson) -> None:
    text = set_field(note_path.read_text(encoding="utf-8"), "updated", today())
    atomic_write(note_path, text.rstrip() + "\n\n" + lesson.as_callout() + "\n")


def approve(v: Vault, draft_id: str, *, rule: str = "", mechanism: str = "", note: str = "",
            words: list[str] | None = None, tools: list[str] | None = None, commands: str = "",
            paths: str = "", klass: str = "") -> Lesson:
    """Turn one draft (and the drafts like it) into a lesson by hand — for what the nightly run left alone."""
    all_d = all_drafts(v)
    d = next((x for x in all_d if x["id"] == draft_id), None)
    if d is None:
        raise KeyError(f"no draft {draft_id}")
    group_ids = next((
        {x["id"] for x in g} for g in groups([x for x in all_d if x.get("status") in ("open", "held")])
        if any(x["id"] == draft_id for x in g)), {draft_id})
    sug = d.get("suggest") or {}
    rule = rule or d.get("rule") or ""
    if not rule:
        raise ValueError("this draft has no rule yet — pass --rule \"<one imperative sentence>\"")
    lib = Library(v.root)
    note_path, note_name = _place(v, lib, rule + " " + d.get("quote", ""), note)
    lesson = Lesson(
        id=unique_lesson_id(lib, rule, klass or sug.get("class") or ""), note=note_name,
        rule=rule, mechanism=mechanism or sug.get("mechanism") or "reflex",
        tools=tools if tools is not None else list(sug.get("tools") or []),
        commands=commands or sug.get("commands") or "", paths=paths,
        words=words if words is not None else list(sug.get("words") or terms(d.get("quote", ""))[:4]),
        since=today(), evidence=short(d.get("quote", ""), 140).replace("\n", " "),
        klass=klass or sug.get("class") or "")
    write_lesson(v, note_path, lesson)
    trust.record(v, [lesson], "approve")
    for x in all_d:
        if x["id"] in group_ids:  # the whole group of similar corrections is answered by this lesson
            x["status"] = "approved"
            x["lesson"] = lesson.id
    save_drafts(v, all_d)
    return lesson


def set_lesson_field(v: Vault, lesson_id: str, key: str, value: str, by: str = "") -> Lesson:
    """Change one field of a lesson callout in place (the rest of the note stays byte-for-byte). "" removes it."""
    lib = Library(v.root)
    hit = next(((n, l) for n in lib.notes.values() for l in n.lessons if l.id == lesson_id), None)
    if hit is None:
        raise KeyError(f"no lesson {lesson_id}")
    note, _ = hit
    lines = note.path.read_text(encoding="utf-8").split("\n")
    head = next(i for i, ln in enumerate(lines) if re.match(rf"^>\s*\[!lesson\][+-]?\s*{re.escape(lesson_id)}\s*$", ln))
    end = head + 1
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    for i in range(head + 1, end):
        if re.match(rf"^>\s*{key}\s*:", lines[i], re.I):
            if value:
                lines[i] = f"> {key}: {value}"
            else:
                del lines[i]
            break
    else:
        if value:
            lines.insert(end, f"> {key}: {value}")
    atomic_write(note.path, set_field("\n".join(lines), "updated", today()))
    lesson = next(l for l in Library(v.root).lessons(retired=True) if l.id == lesson_id)
    trust.record(v, [lesson], by or f"set {key}")
    return lesson


def reject(v: Vault, draft_id: str, block_words: list[str] | None = None, reason: str = "") -> bool:
    all_d = all_drafts(v)
    ids = next(({x["id"] for x in g} for g in groups([x for x in all_d if x.get("status") in ("open", "held")])
                if any(x["id"] == draft_id for x in g)), {draft_id})
    ok = False
    for d in all_d:
        if d["id"] in ids:
            d["status"] = "rejected"
            ok = True
    save_drafts(v, all_d)
    if ok and block_words:
        block(v, block_words, reason)
    return ok


def block(v: Vault, words: list[str], reason: str = "") -> None:
    """Never draft this topic again."""
    append_jsonl(ledger.own(v, "rejected"), {"t": now_iso(), "words": words, "reason": reason})
