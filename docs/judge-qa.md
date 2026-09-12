# Judge Q&A

Each answer: **one sentence first.** Then the detail, only if they want it.
Do not read these aloud — they are rehearsal material, not a script.

The rule underneath all of them: if a claim isn't in
[`what-ships-today.md`](what-ships-today.md) §1, say "not yet — here's what
is."

---

## "Isn't this just a heartbeat with memory?"

**A heartbeat gives an agent another turn. Ora decides whether a turn
should exist at all.**

A heartbeat is `schedule → cognition`: a timer fires, the model wakes, the
model produces something. The decision to act was made by a clock.

Ora is `cognition → intention → schedule → cognition`. It notices something
significant while thinking about the room, forms an intention, chooses when
that should matter — and **at fire time re-reads the room and may decline.**

You saw that at step 5. The reminder was scheduled on Signal. The plan
changed on WhatsApp. The pending decision was withdrawn before it fired. A
timer cannot do that, because a timer does not know it made a decision.

*The other half of the answer:* a heartbeat with memory still speaks every
time it wakes. Ora wakes constantly and almost always says nothing. The
interesting output is the silence, and silence is what a heartbeat has no
way to produce deliberately.

---

## "Why not just put a chatbot in the group chat?"

**A chatbot answers messages. Ora answers situations.**

A chatbot in a group chat has one trigger — a message addressed to it — and
one scope — that message. Three things in the demo are structurally outside
that:

- It stayed quiet for ninety seconds and then spoke because something it
  was tracking came due. No message triggered it.
- It closed a note it was holding because a *human's* answer satisfied the
  condition. That is state changing, not a reply being generated.
- It saw a decision it had made in one app while reasoning in another, and
  withdrew it.

And the practical version: a group chat with a bot in it that replies to
everything is worse than a group chat without one. The value here is
almost entirely in restraint, and restraint is the one thing the chatbot
shape cannot express.

---

## "Why Honcho?"

**It's the one piece that's about people rather than conversations, and
it's the smallest of the four memories on purpose.**

Ora's own stores handle what happened (the transcript), what's unresolved
(notes) and where a room stands (standing). None of those are about a
*person* across rooms and across time. Honcho is — it accumulates an
interpretation of the individuals, and it's self-hosted next to Postgres so
the group's data stays on the group's machine.

It is read on the tag path (a card about itself, a card about whoever
tagged it) and fed at observe. **It is explicitly not authoritative.** If
Honcho and the transcript disagree, the transcript wins — it's a record and
Honcho is an interpretation.

*If they push:* it is not load-bearing for the demo. Turn it off and the
loop still runs. That is deliberate — a memory component that can take down
the loop is a memory component in the wrong place.

---

## "What if the memory is wrong?"

**Then it's an interpretation that's wrong, and the design already assumes
that — which is why interpretation and record are separate stores.**

- The **transcript** cannot be wrong. It is what was typed, with a
  timestamp.
- The **standing** paragraph absolutely can be. So it is rewritten from the
  transcript on a fold rather than patched in place, it is rendered **with
  its age attached**, and a stale position reads as stale rather than as
  fact.
- A **note** carries a closing condition, which means it can be checked
  against reality and retired. A note that was wrong gets closed as `moot`
  — that happens in step 5.
- Every model-written row points at the `model_calls` row behind it: the
  stage, the prompt digest, the tokens, the latency. **A wrong standing
  paragraph is one join away from the exact input that produced it.**

And the brake: a `speak` that cannot cite what it is grounded on — a note
id, a standing clause, a tool result — is downgraded to a hold. It does not
get to say something confidently out of a memory it can't point at.

---

## "Why notes *and* standing? Isn't that the same thing?"

**Standing is where the conversation is. Notes are what's still open. One
goes stale; the other gets closed.**

| | standing | note |
|---|---|---|
| shape | one paragraph, per room | a list, per circle |
| answers | "where does this conversation stand?" | "what has the group left unresolved?" |
| lifecycle | rewritten wholesale on a fold | opened, then closed / expired / moot |
| carries | an age | a **closing condition** |
| when wrong | goes stale, gets rewritten | gets retired with a reason |

Collapse them and you lose the closing condition, which is the thing that
lets the agent *finish* with something instead of remembering it forever.
An assistant that never stops tracking anything is an assistant that
becomes noise.

The deeper rule is the same one behind the transcript/standing split:
**record is separate from interpretation.**

---

## "How do you stop it becoming annoying?"

**The default is silence, and every path to speaking has a brake on it.**

Six of them, all in code, each with a test that reddens when the line is
removed:

1. **Silence is the default.** A hold requires no text and no justification.
2. **A relevance gate** sits in front of the participation turn and judges
   whether the window is worth contributing to at all. In step 6 it says
   `no_go` three times while the room chats.
3. **Grounding.** A speak that can't cite what it's grounded on becomes a
   hold.
4. **A cooldown** after it speaks.
5. **Strikes.** Raise something, get no answer, and it counts — it does not
   raise the same thing indefinitely.
6. **Cross-room cancellation.** A pending reminder is withdrawn when the
   situation changes somewhere else. That is step 5.

The measured version: the gate's instruction ships with 12 evaluation cases
and a runner (`cases/run_gate.py`). Six of those are safety cases where the
correct answer is silence. **You can run them and see the number.**

---

## "How is privacy handled?"

**The group's data lives on a machine the group owns; only the text needed
for a given judgement leaves it.**

- Every message, note, standing paragraph and decision is in a Postgres
  instance on the host — not a vendor's database. Honcho is self-hosted
  next to it.
- Ora is **deny-by-default on rooms**: it only hears and only speaks in
  rooms explicitly listed in a config file. A message from anywhere else is
  dropped at observe, and a send to anywhere else is refused at act. The
  test for this reddens if the line is removed.
- It speaks **as the group's own account**. That is a privacy property, not
  a trick — there is no third-party bot account in the group with its own
  access.
- Images: the bytes are stored on local disk, keyed by their hash. A vision
  model describes them, and the decision-making model reads only the text
  description. **Raw image bytes never reach the model that decides.**

**Be precise about what does leave:** inference runs on Ollama Cloud. The
transcript window, the standing paragraph and the notes being judged are
sent to that endpoint. Ora does **not** run locally or offline, and saying
otherwise would be false.

*If asked what you'd change for real deployment:* local inference is the
obvious one, and the architecture is indifferent to it — the model is a
function called on state. Nothing about the loop assumes where it runs.

---

## "Does it record everything?"

**Yes, in the rooms it's allowed in — and no, not anywhere else.**

It stores the messages in exactly the rooms listed in its config, which is
the same thing every group chat client on your phone already does. What
makes that a design position rather than a shrug:

- The room list is deny-by-default and small. It is not "all your chats".
- The store is on the group's own machine.
- What it **infers** is deliberately small and bounded: one paragraph per
  room, a short list of open items, and a decision log with no model prose
  in it — just `who decided / what / about which note / when`.
- Its own words can never become a note. It cannot bootstrap a belief out
  of something it said.

The honest framing for a judge: this is the same trust you extend to any
group-chat client, pointed at two rooms, with the data on your own box.

---

## "Is the agent persistent? Is it always running?"

**The process is; the agent's thinking isn't — and that's the point.**

There is a long-running process observing the rooms, and there is **no
long-lived model session anywhere.** Every reasoning turn is fresh: it is
called on state, it produces state, it ends.

Which means: kill the process mid-run and restart it, swap the model,
change a prompt — the group's context is untouched, because it was never
inside the model. It was in the transcript, the standing paragraph, the
open notes and the decision log.

> **The model is disposable; the cognition is not.**

That is the strongest claim in the project, and it's testable: the journey
test runs all six steps against a *fake* model client. The loop's behaviour
survives the model being replaced with a stub.

---

## "Why an agent? Couldn't rules do this?"

**Rules can fire a reminder. They cannot decide whether a reminder should
still be sent.**

Take step 5 concretely. A rule would have to recognise that
「唔使諗啦，聽日唔食飯，改咗下星期」 — in Cantonese, in a different app,
with no keyword overlap — settles an open item that was raised elsewhere,
and that a scheduled message about it should now be withdrawn. That is a
judgement about meaning, not a pattern match.

Same at the gate. "Is this conversation at a state worth contributing to?"
has no rule form. Keyword triggers give you a bot that replies when you say
its name, which is the thing we're specifically not building.

**Where rules *are* used, deliberately:** the room allowlist, the
cooldown, the own-message drop, the fail-closed paths, the grounding
requirement. Every brake is a rule. The model judges; the rules constrain.
Nothing about whether it is *allowed* to speak is left to the model.

---

## "What's actually new here?"

**Three things, in order of how load-bearing they are.**

1. **Attention selection as a first-class stage.** The question in front of
   the reply isn't "what should I say?" but "is there an occasion to say
   anything?" — judged per window of conversation, logged either way. The
   silence is an output with a row behind it.
2. **Cognition-initiated proactivity.** Not a schedule that triggers
   thinking, but thinking that produces an intention, schedules it, and
   **re-reads the room at fire time and may decline.** Cross-room, so a
   decision made in one place can be withdrawn from another.
3. **Continuity that isn't in the model.** Four separated stores — record,
   interpretation, open state, decision history — read and written at
   defined seams, so every turn is disposable and nothing is lost when it
   ends.

*What is not new, and don't claim it is:* group-chat integration, RAG,
summarisation, scheduled messages. All of those exist. The composition —
and specifically the decision to make *not speaking* the designed output —
is the contribution.

---

## "How much of this actually works today, versus being a concept?"

**Be exact here. This is the question that decides whether everything else
is believed.** Full detail in
[`what-ships-today.md`](what-ships-today.md).

**Working, verified live today:**
Signal and WhatsApp, both real accounts in real groups. Postgres with
pgvector and self-hosted Honcho, both running. The observe path proven end
to end on WhatsApp. Deny-by-default room allowlist and the own-message
drop, both proved by reverting the line and watching a test go red. Image
comprehension — stored by hash, described by a vision model, with zero raw
bytes reaching the decision model. Exa web search and Google Routes, both
live-tested. Full tracing: every model-written row points at its
`model_calls` row, a gate log row per judged window, a decision log with
four writers and no model prose. One decision model throughout, on one
endpoint.

**Landing during the build window, not yet proven:**
the relevance gate, the participation turn, the proactive decide/act pair,
the orient stage (standing fold, note extraction, curation), and the tag
path. The six demo steps all depend on these. `tests/test_journey.py` has
six strict-xfail markers — one per step. Those markers have now been
removed because all six pass; they pass against a fake model, so they
prove the stages wire together, not that the demo ran live.

**Not built at all:** Discord, location or presence, local inference,
voice. If you've seen those in an earlier project of mine, they are not in
this one.

*The one-sentence version:* "Every input and every store is live and
verified. The deciders were written in the build window, and the six
journey markers are how you tell which of them are real."

---

# Bonus questions to have an answer for

## "Why Signal *and* WhatsApp?"
Because the interesting behaviour is cross-platform and you can't
demonstrate it on one. Step 5 only exists because the reminder was
scheduled in one app and the correction arrived in the other. A single-app
version of Ora is a much less interesting claim.

## "What happens when the model is down?"
It fails closed to silence, everywhere. Gate error → `no_go`. Turn error →
hold. Proactive error → decline. Send failure → the row is marked failed
and nothing is retried into the room. And the distinction is visible: a
dead model writes an `error_kind`, never a real `no_go`, so you can always
tell a judgement from an outage.

## "Can it hallucinate into the group?"
It can be wrong, like any model. What it cannot do is speak *ungrounded* —
a reply that can't cite a note, a standing clause or a tool result is
downgraded to silence before it is sent. That is one line of code with one
test that reddens when you remove it.

## "Why is the demo in Cantonese?"
Because that's what the group actually types. The prompts and the eval
cases were measured on mixed Cantonese and English, which is the real
input, not a translated one.

## "Does this scale to a big group / a company?"
Not as built, and it isn't trying to. It's scoped to a circle — small
enough that one paragraph per room is a meaningful summary and one short
list of open items is the whole social state. A hundred-person channel
breaks both assumptions. That's a different product.

## "What would you do next?"
Measure the gate against the full case suite instead of the twelve-case
subset; wire the second judging pass that's copied in but not connected;
local inference. And the thing that actually matters: run it on a real
group for a fortnight and count how often it spoke when it shouldn't have.
Nothing else tells you whether it works.

## "What would show this was wrong?"
A fortnight in a real group where the speak/stay-quiet judgement doesn't
beat a keyword trigger. If the gate's judgement isn't better than "reply
when named", the whole premise fails — and that is measurable, not
arguable.
