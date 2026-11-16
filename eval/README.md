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
