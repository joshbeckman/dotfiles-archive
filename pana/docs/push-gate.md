# The push gate's question

Pana used to push every delivery to Josh's phone. A session's one-line status report and a question
only he could answer both buzzed it. The push gate asks a System One model one `noul` question
about each delivery and skips the push below `PUSH_GATE_THRESHOLD` (`src/decisions/push.ts`,
0.6). Skipping is silence, never loss: the SSE publish is unconditional, and the message is in the
inbox unread either way.

The wording and the threshold were measured on this domain's own mail. That work is below, because
the number that matters is not "how many pushes did we save" but "how often was the saving free".

## The corpus

Read from the live daemon's API, not from the database and not written by hand:

1. Page `GET /inbox?archived=true|false` as the user, 50 at a time, and keep every delivery whose
   sender is a session or the domain. That is every message pana delivered to Josh.
2. Window: 2026-10-27 21:43Z to 2026-11-14 05:17Z, 18 days, 914 deliveries.
3. Dedupe by normalized text: 816 survive. 92 of the duplicates are one scheduled-wake notice
   repeated 93 times; it collapses to one example.
4. For each delivery, compute the latency to Josh's first read: `deliveredAt` to `readAt` from the
   inbox row (the delivery's own `read_at`, which pana sets when he opens the thread or marks the
   message read). `null` is "never read".

The state a push carries is the delivery rendered exactly as a wake renders it (`deliveryText` in
`src/threads/render.ts`): the `New message. / From: / Thread: / Message: / Subject:` frame, then
the body. The frame is not decoration; it measurably changed the wake gate's answers.

## The latency, before any modeling

| set | n | p50 | p90 | ≤5 min | ≤15 min | ≤60 min | never |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | 816 | 4.5 min | 15.5 h | 46% | 57% | 67% | 8% |
| from a session | 810 | 4.2 min | 12 h | 47% | 57% | 67% | 8% |
| a domain notice | 6 | 2.5 days | 3.0 days | 0% | 0% | 0% | 0% |

That is the cost of the status quo: for 33% of what pana pushes, Josh does not look for an hour;
for 8% he never looks at all. The domain rows are notices, not session mail: 104 arrived, not one
was read within the hour, and the median was 2.9 days. They still push (see the open question
below).

## Why read time cannot be the label

The obvious rule is the one above: read in a few minutes means the push was wanted, unread for
hours means it was not. It is wrong, and the data says so plainly.

Pana can tell whether Josh was at his devices at the moment of delivery: 553 of 816 deliveries
(68%) arrived within ten minutes of a previous read.

| at delivery | n | p50 | ≤5 min | ≤60 min | never |
| --- | --- | --- | --- | --- | --- |
| Josh had just read something | 553 | 1.9 min | 60% | 81% | 6% |
| Josh had not | 263 | 94.5 min | 18% | 37% | 14% |

A message he reads in ninety seconds while he is already in the thread did not need a push: the
open window's SSE event already showed it. Read time measures whether he was present, not whether
the message was worth an interruption. A gate scored against it learns the wrong thing, and it
shows: every wording below ranked at 0.5 AUC or worse against the latency label, and most ranked
*below* 0.5, because mail that arrives while he is away looks more urgent than the headless "ok"
replies he reads in seconds.

So the label comes from reading the messages.

## The labels

Two sets, from one reader (me), on the question the gate actually asks: *would Josh want his phone
to buzz for this message as it arrives?*

- **Yes** when the message waits on him: a merge, an approval, a decision, an answer, or a fix, or
  it reports a failure or a blocked state in his own systems.
- **No** when he can read it whenever he next opens the app: reports of finished work, research
  and coaching answers, summaries, nightly digests, relays, courtesy.

**200 labels, a uniform random sample of the 816**, which is the number every precision and recall
below is computed on. 43 are yes (21.5%). That base rate is the whole problem: most of what pana
pushes is not worth an interruption, and the gate has to find the fifth that is.

A middle band of 224 deliveries (read between 5 minutes and 4 hours) was also read and labeled, to
decide the latency rule's ambiguous middle; 6 of those were not wanted (five nightly critic
digests, one agent-to-agent relay). Those labels are reported here but not pooled with the 200,
because the band is not a uniform sample.

The two label sets disagree hard, which is the finding: of the 98 sampled deliveries Josh read
within five minutes, 83 are "not wanted". He read them fast because he was already there.

## The measurement

Ten wordings, each run twice over all 816 deliveries, at 24 concurrent requests, 16,320 calls
against `accounts/fireworks/models/rune`. Every call went through the real
`POST https://api.fireworks.ai/inference/v1/systemone` with the payload pana sends: same state
text, same `noul` question shape, same 2-second budget.

| # | wording, in short | AUC run 1 | AUC run 2 |
| --- | --- | --- | --- |
| w1 | "need to interrupt the recipient now with a notification" | 0.715 | 0.722 |
| w2 | "urgent enough to buzz their phone right now" | 0.626 | 0.610 |
| w3 | "would they want to see it within a few minutes" | 0.615 | 0.672 |
| w4 | "worth a phone notification that pulls them back", with criteria | 0.656 | 0.672 |
| w5 | "need the recipient's attention within minutes" | 0.632 | 0.641 |
| w6 | w1 plus "when in doubt answer yes" | 0.676 | 0.667 |
| w7 | "is the sender asking or reporting" | 0.664 | 0.671 |
| w8 | "would a busy person want their phone to buzz" | 0.585 | 0.574 |
| w9 | **w1 plus "a pull request open and waiting on their merge, a decision only they can make, or work blocked on them"** | **0.806** | **0.806** |
| w10 | "is the message waiting on the recipient", with a no-list | 0.786 | 0.783 |
| w11 | w9 plus "or an answer to a question they asked" | 0.800 | 0.797 |
| w12 | **w9 plus "a result they asked to see, including an answer to their question or work they asked for that is now done or ready"** | 0.792 | 0.788 |

AUC is over the 200 hand-labeled deliveries. w1 alone ranked mail at 0.72, and its one systematic
miss was a shape: "PR 306 now has green checks, ready for your merge" scored below 0.01 and read as
finished work. Adding the clause that a pull request waiting on his merge *is* a request fixed it;
w9 is that wording and it is what ships. w10 is nearly as good and pushes far more, because its
no-list still leaves "reports of finished work" at high probability.

Thresholds 0.05 through 0.5 were swept, and then the sweep was extended to 0.8 because w9's answers
sit high. Cost is counted on 200 messages with a missed push weighted `w` and a useless push
weighted 1; pushing everything is 157 useless pushes and no misses.

| threshold | pushes kept | precision | recall | misses | useless | cost at w=3 | cost at w=5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05 | 127 | 0.33 | 0.98 | 1 | 85 | 88 | 90 |
| 0.2 | 92.5 | 0.35 | 0.76 | 10.5 | 60 | 91.5 | 112.5 |
| 0.4 | 77.5 | 0.41 | 0.74 | 11 | 45.5 | 78.5 | 100.5 |
| 0.5 | 69.5 | 0.45 | 0.73 | 11.5 | 38 | 72.5 | 95.5 |
| **0.6** | **64.5** | **0.49** | **0.73** | **11.5** | **33** | **67.5** | **90.5** |
| 0.7 | 58 | 0.48 | 0.65 | 15 | 30 | 75 | 105 |
| 0.8 | 46 | 0.47 | 0.50 | 21.5 | 24.5 | 89 | 132 |

**Chosen at the time: w9 at 0.6.** It kept 32% of what used to push, at precision 0.49 and recall
0.73. That is not what ships; the second measurement below replaced it.

Intervals, bootstrapped over the 200 examples (4,000 resamples, 90%): precision 0.49 [0.39-0.59],
recall 0.73 [0.62-0.85]. Run to run, the two passes agree on 189 of 200 decisions at 0.6, and the
counts differ by about two messages at every threshold in the sweep.

## The weighting, said plainly

A missed push costs Josh silence on something he wanted now. A useless push costs him an
interruption, several times an hour, and it teaches him to ignore the phone. The message is still in
the inbox after a miss, so the cost of a miss is delay, not loss. I first weighted the miss at three,
and the shipped w9 at 0.6 was that number's answer.

Josh's complaint is the correction: he would rather be buzzed ten times for nothing than miss the
answer he asked for, so the weight is ten. This section keeps the 3:1 arithmetic because it is what
shipped first and what the audit's tuning language still refers to.

At 3:1 the gate costs 67.5 mistakes per 200 messages against 157 for pushing everything: two thirds of
the interruptions go, at a cost of 11.5 delayed messages per 200 (about six of them things he has to
act on, because the miss count includes the label's own noise). The gate keeps paying until a missed
push is worth five useful ones: at 5:1 it is 90.5 against 157, still a win; at 10:1 it would be a
loss (276 against 157). If Josh would rather never miss anything, the threshold is the wrong
instrument; unset `decisionModel`.

## The second measurement: the gate went too quiet

Two days of live traffic reopened the question. Josh said the gate pushed him nothing: 14 gated
deliveries in 13 hours, one push. The skips included messages he had asked for - "Done. The artifact
on this thread is now v3", "Yes. The transcript blocks are plain HTML", "Yes. The artifact is on this
thread. Press play", and an answer naming a domain - all scoring 0.002 to 0.053 under w9.

I rebuilt those 15 deliveries' states and re-ran w9 over them: the harness reproduced the daemon's
numbers (0.002 for 0.000, 0.867 for 0.852, 0.042 for 0.042), so this is the question's judgement and
not a plumbing bug. The four he named score at most 0.053: no threshold that leaves a gate at all
recovers them. Adding them as wanted labels (204 total, 47 wanted) and weighting a miss at 10:1 -
the correction to my 3:1 guess, which his complaint disconfirmed - gives:

| threshold | w9 precision | w9 recall | w9 pushes | w9 cost at 10:1 | w12 precision | w12 recall | w12 pushes | w12 cost at 10:1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.01 | 0.30 | 0.96 | 151.5 | 126.5 | 0.24 | 1.00 | 198.5 | 151.5 |
| 0.02 | 0.31 | 0.94 | 142 | 128 | 0.24 | 1.00 | 193.5 | 146.5 |
| 0.03 | 0.32 | 0.94 | 135.5 | **121.5** | 0.25 | 0.99 | 184.5 | **143** |
| 0.05 | 0.33 | 0.89 | 127 | 135 | 0.25 | 0.91 | 170.5 | 167.5 |
| 0.08 | 0.33 | 0.81 | 116 | 168 | 0.27 | 0.87 | 154 | 173 |
| 0.09 | 0.33 | 0.79 | 113 | 173.5 | 0.27 | 0.86 | 151.5 | 176 |
| 0.10 | 0.33 | 0.76 | 109 | 188.5 | 0.27 | 0.84 | 147.5 | 183 |
| 0.20 | 0.35 | 0.69 | 92.5 | 205 | 0.28 | 0.77 | 127.5 | 201.5 |
| 0.30 | 0.39 | 0.69 | 83.5 | 196 | 0.31 | 0.77 | 117.5 | 191.5 |
| 0.60 | 0.49 | 0.67 | 64.5 | 188 | 0.34 | 0.68 | 94.5 | 212.5 |

Pushing everything costs 157 at 10:1, so the cost optimum is 0.03 for every wording: at that weight
the honest answer is "push almost everything". A threshold that low is not a gate, so the number
ships from the second rule instead: the highest cut that still recalls 85% of the wanted pushes.
For w9 that is 0.05, and it still scores all four of his examples below the line (0.002 to 0.042), so
the wording has to change with it. For w12, the wording that names his shape, the crossing is 0.09:
0.10 recalls 0.84 and 0.09 recalls 0.86.

**Chosen: w12 at 0.09**, and since the next day that number and the wake gate's are config (`gates.push`, `gates.wake`; PSH-11), because Josh's reaction to the first tuning round was "why do we have to ship a PR to tune these numbers?". The wording stays in code, where it wants review; the number is now an edit and a restart. 151.5 of 204 pushes (74%), precision 0.27, recall 0.86. The same clause
lifts his four messages from 0.002/0.018/0.004/0.042 to 0.438/0.867/0.165/0.047, so three of the
four now push and the fourth (a one-line answer naming `tools.joshbeckman.org`) sits just under.
On the 13 hours of live traffic that prompted the change, 8 of 15 deliveries would push instead of 1.

The finding to say out loud: at this recall the filter is nearly worthless. No w12 threshold has
recall above 0.8 *and* precision above 0.3 - the best precision in that band is 0.28 - and the 0.27
against a 0.23 base rate is a 1.2x lift. What the gate still removes at 0.09 is the shape my labels
call noise and Josh does not miss: routine checks that found nothing, "still nothing yet" statuses,
nightly digests, and unprompted coaching. That is worth having and it is not the thing the first
measurement claimed.

## What is still wrong

- **One labeler, 204 examples.** The precision interval is ±0.10 wide. A second reader on the same
  texts is the next thing to add; the labels are the measurement here, so their bias is the
  measurement's bias.
- **My rubric under-counted the shape Josh complained about.** I labeled a short "here is the thing
  you asked for" as a report, so the first wording learned to skip it and precision was measured
  against a target that disagreed with him. The four live examples are now labeled wanted; the rest
  of the 200 still carry the old rubric, so precision at 0.09 is a floor, not an estimate.
- **The filter is nearly worthless at this recall.** Precision 0.27 against a 0.23 base rate is a
  1.2x lift. No threshold gets recall above 0.8 with precision above 0.3. The gate now earns its
  keep by removing the shapes he does not miss (routine checks, "still nothing", digests, coaching),
  not by being a good classifier of the rest.
- **The model reads actions as reports and coaching as urgency.** The misses that remain are a
  checkout behind master ("pull to get the fix") and a pull request waiting on him; the false
  positives are a training critique ("you've hit the line on leg training") and an instruction
  written from one session to another. Both shapes look like an ask in the state text and are not.
- **No time of day.** The state carries no timestamp, so a message delivered at 03:00 is judged as
  if Josh were awake. Notices that arrive overnight are the largest single block of misses waiting
  to happen; the audit cron looks for exactly that (a kept push he read hours later).
- **An objective escalation has no measured example.** It is a domain *delivery*, so the sender
  rule keeps its push whatever the question says. That rule is a judgement, not a measurement.
- **Presence is invisible to the gate.** The best predictor in the data is not the text at all: it
  is whether he already had a window open. Skipping pushes for an open window was rejected because
  a suspended iOS app keeps a dead SSE connection that looks open, and that failure mode is Josh
  missing everything.
- **Whether notices may ever be gated is open.** The 6 unique notice texts all scored below 0.15,
  and their latency says nobody looked for hours. That is not enough to silence them: `postNotice`
  carries a session's failed run, a session stopped for its budget, the daily budget stop that
  halts every run until tomorrow, and objective metric-check failures. They read as reports because
  they are reports, and a pooled score over six samples cannot justify a budget stop arriving
  quietly. They push today. Closing this needs the notice shapes collected separately and labeled
  by whether they would be wanted at 03:00, not one pooled threshold over all of them.

## Re-running it

The harness is ~80 lines of Python and stays in the session's scratch: fetch `/inbox` with the
domain's web token, dedupe by text, render each delivery the way `deliveryText` does, label, then
POST `{model, state, questions}` to the systemone endpoint under `S_KEY_COMMAND` from the `s` store.
Keep the labels, run every wording twice, and rerun after any change to the wording, the model, the
provider, or the mix of mail.
