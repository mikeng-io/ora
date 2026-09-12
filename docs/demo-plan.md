# Demo plan — one run, six steps, replayable

One scenario. It runs the same way in rehearsal, in the video, and live on
stage. It is frozen as data in [`cases/journey.yaml`](../cases/journey.yaml)
and as six tests in `tests/test_journey.py` (their `xfail` markers removed
once each passed) — those
markers are the scoreboard. The exact messages, timings and verification
queries are in [`RUNBOOK.md`](../RUNBOOK.md); this document is the version
you read before walking on stage.

**Read [`docs/what-ships-today.md`](what-ships-today.md) first.** Every step
below depends on code that was still landing at 11:36. The dependency
column says which.

---

## 0. Before anything runs

### The rooms

Two real group chats — a Signal group and a WhatsApp group — with the same
people in both. Ora is a linked device on each account and **speaks as the
account**. There is no bot badge. Say this out loud once, early; it is the
first thing a judge will wonder about and it is better volunteered than
extracted.

### The content is scripted, and says so

The group is real. The messages in this run are **written for the demo** —
plans about tomorrow, a meeting time, dinner. Nothing private is on screen
and there is no real chat history in the frame.

**Before recording or presenting: both chat windows are scrolled so only
the demo run is visible.** Not because anything is embarrassing, but
because a judge reading someone's actual group chat is a worse experience
than a clean window, and because a screen recording is forever.

### The seed, disclosed

Two notes exist before the run starts, created visibly in advance:

| | note | closes when | about |
|---|---|---|---|
| **N1** | Cyberport tomorrow — meeting time not fixed | a time is agreed | tomorrow 10:00, Cyberport |
| **N2** | Dinner after Cyberport — where? | a place is agreed | tomorrow 19:00 |

**Disclose this in the first line of the video and the first time the notes
appear on stage.** The sentence is:

> "We seeded two open notes before this run. It speaks when it's holding
> something — today, it held these."

A demo that manufactures the agent's grounding and hides it is a demo that
lies. Saying it costs four seconds and buys the whole rest of the run.

### The clocks

Everything is compressed for the stage: a window settles in 15s, the
proactive pass wakes every 30s, a reminder fires 60s after it is decided.
In real use these are minutes and hours. **The clocks are frozen after
rehearsal #1 — do not tune them on the day.** If a step is slow, it is slow
in the video too; that is what trimming is for.

### Screen layout

| left third | middle third | right third |
|---|---|---|
| Signal Desktop | WhatsApp Web | `python -m loop_tail` |

Phones are senders only. They never appear on screen.

### Pre-flight, in order

```sh
uv run ruff check . && uv run pytest -q     # green: the whole suite, six journey steps included
docker compose ps                           # postgres, honcho, signal-cli, whatsapp-bridge all up
python -m loop_tail                         # N1 and N2 visible, both open
# send one throwaway message in each room, confirm two OBSERVE lines appear, delete nothing
```

If `loop_tail` does not show both seeded notes, **stop**. Step 1 cannot
work and everything after it inherits the failure.

---

## The six steps

Times are from the start of the run (`t0`), not wall clock.

---

### Step 1 · t 0:00 · nobody called it

**A, on WhatsApp:** 「聽日幾點去 Cyberport？」
*(What time are we going to Cyberport tomorrow?)*

Nobody tags anything.

**What the audience sees:** the window settles, `loop_tail` prints
`GATE · WhatsApp · go`, then `TURN · WhatsApp · spoke · grounded note:N1`,
and the answer appears in WhatsApp.

**What proves it:** a `gate_log` row with verdict `go`, and a `decisions`
row `participation / spoke` with `grounded_on = note:N1`. Not just "it
replied" — **grounded on the note it was already holding.**

**Say:** "Nobody called it. It had a note open on this, so it answered."

**Depends on:** gate + participation turn + runtime *(in flight)*

**If it fails:**
- *Silent (gate `no_go`, or turn `hold`)* — check the note rendered into
  the turn's input. If it still holds, **turn step 1 into a tag**: Mike
  tags it with the same question. You lose the "nobody called it" beat here
  but keep it at step 4, which is the stronger one anyway. Say: "It held on
  that one — let me call it directly," and move on without apologising.
- *It speaks but ungrounded* — it won't; the brake downgrades an ungrounded
  speak to a hold. If you see a `hold` with reason `ungrounded`, **that is
  a brake firing and it is a better story than the reply was.** Point at it.

---

### Step 2 · t 0:40 · called, on the other app

**Mike, on Signal:** 「@ora 咁聽日 10 點集合，點去？」
*(So we meet at 10 tomorrow — how do we get there?)*

**What the audience sees:** `TAG · Signal`, then a reply with a real route
and one web result. Behind it, `FOLD · Signal · standing rewritten`.

**What proves it:** a `decisions` row `tag / replied`; `standing` for the
Signal room has a new `updated_at`.

**Say:** "When it's called, it answers — and notice the answer just fixed
the time."

**Depends on:** tag path *(in flight)*; Routes and Exa *(live-verified)*

**If it fails:**
- *Tools fail (no route, no web result)* — it still answers from what it
  knows. A tool failure degrades, it does not crash. Keep going; the route
  is garnish, the tag path is the point.
- *No reply at all* — skip to step 3 and say the tag path is the least
  interesting half. Do not retype the tag live; a second identical tag
  makes the room look broken twice.

---

### Step 3 · t 1:00 · it closes its own note, citing a human

Silent step. Nobody types. Mike's tag at step 2 fixed the time, so the
thing N1 was tracking is settled.

**What the audience sees:** `CURATION · N1 closed · cites row <n> (Mike)`.
In `loop_tail`, N1 moves out of the open list.

**What proves it:** `note N1` has `retired_reason = closed` and its
`cited_row_id` points at **Mike's message, a human row** — never at Ora's
own.

**Say:** "It's closing what it was tracking — and it's citing Mike's
message, not its own. It doesn't get to mark its own homework."

**Depends on:** orient / curation *(in flight)*

**If it fails:** N1 stays open. Nothing downstream breaks — step 4 is about
N2. Do not draw attention to it; if a judge notices, say curation runs on
a tick and hasn't come round yet, and show the note list instead.

---

### Step 4 · t 1:00–2:30 · it starts the conversation

Silence. Nobody types for ninety seconds. **Let it be quiet — do not talk
over this.** The pause is the demo.

**What the audience sees:** `PROACTIVE · remind N2 → R1 due …` appears in
`loop_tail` while nothing happens in either chat. Then, at the scheduled
moment, 「晚飯食邊度？」 *(Where are we eating dinner?)* arrives in the
Signal room — the home room, not the one the last message was in. Then
`strikes N2 = 1` and a second reminder R2 scheduled.

**What proves it:** a `reminders` row R1 `pending` then fired; a `decisions`
row `proactive_act / spoke`; N2's strike count at 1 and R2 `pending`.

**Say:** "Later — compressed to seconds here — it raises the one thing
still open. Once. In the room where it came up. Nobody prompted that."

**Depends on:** proactive decide/act pair *(in flight, not landed at 11:36
— highest-risk step)*

**If it fails:**
- *Never fires* — check `loop_tail` for a `pending` row. No row means the
  decide turn declined; read its reason code aloud, because "it decided not
  to" is on-message, not a failure. Then run
  `uv run pytest tests/test_journey.py -k step4` on screen and say you are
  showing the covering test because the live tick didn't come round.
- *Fires in the wrong room* — say so. It is a bug, not a lie, and naming it
  costs less than hoping nobody read the room label.

---

### Step 5 · t 2:40 · it changes its mind, across apps

**A, on WhatsApp:** 「唔使諗啦，聽日唔食飯，改咗下星期」
*(Never mind — no dinner tomorrow, we moved it to next week.)*

The correction arrives on **WhatsApp**. The pending reminder R2 was
scheduled on **Signal**.

**What the audience sees:** `TURN · WhatsApp · cancel R2 (saw it in
loop_decisions)`, R2 flips to `cancelled` in `loop_tail`, N2 goes `moot` —
and **nothing is ever said in Signal**.

**What proves it:** `reminders.R2.state = cancelled`; a `decisions` row
`participation / cancelled` with `reminder_id = R2`; **zero** Ora messages
in the Signal room after this point.

**Say:** "The correction came on the other app. It saw its own pending
decision — made on Signal — and dropped it. No second raise."

**Depends on:** participation turn + the decision log rendering across
rooms *(in flight)*

**If it fails:**
- *R2 fires anyway (a double raise)* — this is the worst visible failure in
  the run. Do not hide it: "That's the failure mode this whole design is
  built to prevent, and it just happened — the pending decision wasn't
  rendered to the other room's turn." Then run
  `uv run pytest tests/test_journey.py -k step5`, which covers exactly this.
  A named failure with a covering test reads as engineering; a mumbled one
  reads as a demo that was never real.
- *Nothing happens at all* — acceptable. The observable claim for step 5 is
  **zero messages in Signal**, and silence satisfies it. Show the
  `cancelled` row if it exists; if it doesn't, say the reminder was
  withdrawn and show the reminders table.

---

### Step 6 · t 3:30 · it says nothing, on purpose

**A, on both apps:** ordinary banter — a link, 「lol」×3. Deliberately
**after** the 60-second cooldown has expired, so the silence is a
judgement, not a timer.

**What the audience sees:** the chats fill up. `loop_tail` prints
`GATE · WhatsApp · no_go`, `GATE · Signal · no_go`, `no_go` — repeatedly.
Nothing is sent.

**What proves it:** `gate_log` rows with verdict `no_go` and
`finish_reason = stop`. **A dead model produces an `error_kind`, never a
real `no_go`** — which is precisely why this step is measured on the rows
and not on the absence of a message.

**Say:** "It's reading every one of these and deciding not to speak. That's
not the agent being off — that's the decision, in a row, with the reasoning
call behind it."

**Depends on:** gate + runtime *(in flight)*

**If it fails:**
- *It speaks* — the honest read is that the gate judged the banter worth a
  contribution. Say so, and say the 12 gate cases with their runner ship in
  the repo for exactly this reason. Don't argue with the model in public.
- *`error_kind` instead of `no_go`* — the model is down and the system
  failed closed to silence. **That is the designed behaviour and it is
  worth saying:** "That silence is the fail-closed path, not a judgement —
  and the difference is visible in the row."

---

## Stage failure protocol

Three rules, in order.

1. **Name it in one sentence and keep moving.** "That step didn't fire —
   here's the test that covers it." Never re-run a step live hoping for a
   different result. A second attempt doubles the dead air and halves the
   credibility.
2. **Never claim a row you cannot show.** If `loop_tail` does not have it,
   it did not happen. Faking a pass is the one unrecoverable mistake on
   this project.
3. **Fall back to the journey test, on screen.** `uv run pytest
   tests/test_journey.py` runs the same six steps against a fake model. It
   is not as good as live and it is infinitely better than a story.

### The cut line

**At 13:15, on rehearsal #1's result:** if steps 4 and 5 are not green,
the video shows **steps 1–3 and 6 live, and 4–5 from the journey test**, and
the calendar stretch is not attempted. This decision is made once, at 13:15,
and not revisited at 14:10.

### Known environment failures and the answer

| what breaks | when we know | what we do |
|---|---|---|
| WhatsApp bridge unstable | 10:30 smoke | Signal-only demo; steps 1 and 5 move to Signal; **the cross-platform claim comes out of the script**, including the step 5 line |
| Ollama Cloud slow or erroring | rehearsal #1 | everything fails closed to silence; the journey test covers the dead step |
| Signal linked device not receiving | 10:00 | re-link from the phone; WhatsApp-only until it's back |
| a mention goes out as plain text | first send | plain text is the designed fallback, not a failure — don't mention it |
| calendar stub | always | not attempted unless 1–6 are green |

---

## Replaying it

The run is deterministic in the sense that matters: the same six messages,
in the same order, against the same seeded notes, on frozen clocks. It is
not deterministic in the model's wording — the reply text will differ
between runs, and the verification is on the **rows**, never on the prose.

To replay from clean:

```sh
# 1. clear the demo workspace's rows (see RUNBOOK §0)
# 2. re-seed N1 and N2 with created_at visibly before t0
# 3. confirm both notes in loop_tail
# 4. send the six messages on the RUNBOOK's clock
# 5. run each step's verification query; write the real row ids into RUNBOOK
```

**The README's demo table is written from rehearsal #2's actual row ids,
never from intent.** An unfilled id in `RUNBOOK.md` means that step has not
been proved, and a step that has not been proved is not claimed.
