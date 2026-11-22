# Does it notice when you correct your agent?

Everything the brain learns starts with one question per message: *did the user just correct the agent, or
say how things should be done?* This folder measures how well it answers that question in six languages.

## The data

[`corrections.jsonl`](corrections.jsonl) holds 250 replies a user gave to a coding agent, written and
labelled by hand: 50 each in English, German, Spanish and French, and 25 each in Portuguese and Italian.

| label | what it is | count |
|---|---|---|
| `correction`, `durable` | the agent got it wrong, and it should become a lasting rule | 50 |
| `correction`, one-off | the agent got it wrong, but only for this task ("make it 20 here") | 44 |
| `preference` | how things should be done from now on | 36 |
| `none` | everything else | 120 |

The `none` replies are hard on purpose. They include replies that only *look* like corrections:

- "no, first to staging" as the answer to the agent's own yes/no question;
- "oops, I meant token.ts" when the user corrects their own instruction;
- "why a set instead of a sorted list? just curious";
- "yep you're right, works now";
- a pasted stack trace;
- a bug report that says "always".

Each line is `{"id", "lang", "agent", "user", "label", "durable"}`. It holds no real user data.

## The method

- **Detector.** `learn.classify` runs on each message exactly as it does when the brain reads a transcript. It uses no LLM. A message is flagged when its score reaches the draft threshold (0.4). A flag is right when the label is `correction` or `preference`.
- **Split.** A hash of the id splits the messages into two halves:
  - **dev** (117 messages): the patterns were tuned on this half only;
  - **test** (133 messages): never looked at while tuning.
- **Published numbers.** All numbers below are test numbers.
- **Judge.** The flagged test messages then go through the real nightly judge (Haiku through the `claude` CLI). This is the step that decides what becomes a rule.

```bash
python3 eval/run.py                  # the detector on the test half
python3 eval/run.py --split dev      # the tuning half, with every miss and false alarm
python3 eval/run.py --judge          # the LLM judge on top (asks first; a few cents)
```

## Results (test half, October 30, 2026)

**Detector, no LLM:**

| language | messages | real | flagged | precision | recall | recall on lasting ones |
|---|---:|---:|---:|---:|---:|---:|
| English | 26 | 13 | 11 | 82 % | 69 % | 86 % |
| German | 34 | 19 | 13 | 100 % | 68 % | 82 % |
| Spanish | 24 | 11 | 9 | 89 % | 73 % | 100 % |
| French | 24 | 14 | 11 | 100 % | 79 % | 88 % |
| Portuguese | 12 | 6 | 4 | 100 % | 67 % | 100 % |
| Italian | 13 | 9 | 6 | 83 % | 56 % | 67 % |
| **all** | **133** | **72** | **54** | **93 %** | **69 %** | **86 %** |

Before this work the detector knew English and German only. On the same test half it flagged 11 messages, all of them right (100 % precision), but it found only 15 % of the corrections and 12 % of the lasting ones.

The tuning half reached 100 % precision and 93 % recall. The gap to the test half is the honest price of hand-written patterns.

**Then the judge (Haiku), on the 54 flagged test messages:**

| what the message was | the judge said | count |
|---|---|---:|
| a lasting correction or preference | rule | 37 |
| a one-off correction | once | 6 |
| a one-off correction | noise | 1 |
| a one-off correction | rule | 6 |
| not a correction | noise | 4 |

- Every false alarm of the detector was thrown out.
- Every lasting correction was kept.
- Of what the judge kept as a rule, 86 % was a lasting one.
- One more filter comes after the judge, in production: a rule needs **two sessions**, or the user said *always* or *never*. A one-off correction rarely comes back in a second session. This eval cannot show that filter, because every message here stands alone.

The judge run cost **$0.012** (3 calls, 39,600 tokens).

## Limits

- 250 messages is a small set: Portuguese and Italian have 12 and 13 test messages each, so one message moves their numbers by 8 points.
- The messages were written to resemble real ones, but they are not real sessions. Real sessions are longer and messier.
- The patterns live in `self_improving_brain/learn.py`. `tests/test_eval.py` fails when the test numbers drop, so a change that helps one language cannot quietly hurt another.

To add a language, add its phrases to the pattern lists in `learn.py` and its messages to the data. Report the test half, never the dev half.

# Does recall find the right note?

The second thing measured here: given a question, does `brain search` (and the prompt hook) rank the note that
answers it first? [`recall.py`](recall.py) asks 123 questions about 40 invented notes ([`recall_vault/`](recall_vault),
English and German) and checks where the right note lands. Each question is tagged by how it differs from the note:
`exact` (shares its words), `form` (another word form: "rolled back" for "rollback"), `compound`, `typo`, `paraphrase`
(shares no word at all).

```bash
python3 eval/recall.py                        # test half: word search against words + 4-grams
python3 eval/recall.py --split dev            # tuning half, with every miss listed
python3 eval/recall.py --thresholds 3,4,5,6   # what the hook would show at each recall_threshold
python3 eval/recall.py --time                 # median search time
```

The split is a hash of the question id. The fusion weights come from the original tuning on real sessions; on this
set `dev` (60 questions) was only used to check the threshold, and `test` (63 questions) is what is reported.

## Results (test half, November 22, 2026)

| questions | n | words: top-1 / top-5 / MRR | words + 4-grams: top-1 / top-5 / MRR |
|---|---:|---|---|
| all | 63 | 86 % / 89 % / 0.87 | **98 % / 98 % / 0.99** |
| exact | 18 | 94 % / 100 % / 0.97 | 100 % / 100 % / 1.00 |
| form | 27 | 93 % / 96 % / 0.94 | 100 % / 100 % / 1.00 |
| compound | 6 | 33 % / 33 % / 0.33 | 100 % / 100 % / 1.00 |
| typo | 6 | 83 % / 83 % / 0.83 | 100 % / 100 % / 1.00 |
| paraphrase | 6 | 83 % / 83 % / 0.83 | 83 % / 83 % / 0.86 |

The recall threshold stays at 4.0. At that threshold the hook shows the right note first for 56 of 63 test questions
(52 with words only) and a wrong one for 1 (1); on dev it shows 49 right and 3 wrong (45 and 5). A lower threshold
(3.0) would show a note for every question but also let 4-gram-only hits through: the signals order candidates, the
words decide whether to show one.

**Limits, read these.** The notes and the questions are invented by the same author, small, and the questions were
written to include word-form, compound and typo mismatches, which is exactly what 4-grams fix: the +12 points show
that the signal works where it should, not what you will gain on your vault (on the maintainer's own real sessions the same idea
gave about +9 points top-1 and +4.6 top-5, with a wide interval). `paraphrase` is where only dense vectors
(`bge-m3` through a local ollama) can help; they are **not measured here** because the model is not part of this
repository, so the dense signal is covered by unit tests only. Cost: the prompt hook is about 10 ms slower per
prompt in a 300-note vault; the first prompt after a note changed rebuilds the 4-gram index (about 0.15 s at 300
notes, warmed by the night).

## A gold set of your own

Numbers on invented notes say little about your vault. [`gold_build.py`](gold_build.py) builds a gold set from your
sessions: it finds the questions after which your agent opened notes (`candidates`, no LLM), lets your LLM CLI judge
which of them were needed (`judge`, budgeted, asks first), and splits the result **by session**, 50 % `dev` and 50 %
`test`, so a conversation is never on both sides.

```bash
python3 eval/gold_build.py candidates --vault ~/brain
python3 eval/gold_build.py judge --vault ~/brain
python3 eval/gold_build.py check ~/brain/.brain/state/gold.jsonl
python3 eval/recall.py --gold ~/brain/.brain/state/gold.jsonl --vault ~/brain
```

The files hold your real questions and stay in `.brain/state/`, which the vault's git ignores. Never publish them;
publish numbers. Report the test half only. The judge is a model, not the truth: read a sample.
