# Slides

Ten slides. Most are one line and one image. The deck is not the argument —
the demo is. The deck exists so the room knows what it just watched.

**One structural change from the obvious running order.** The suggested arc
was *problem → behaviour → loop → why not a heartbeat → thesis*. Two
changes:

1. **The demo goes at slide 4**, before a single word of architecture. Show
   it speaking unprompted, and *then* let the room ask how. A diagram shown
   before the behaviour lands is a diagram nobody reads.
2. **The silence slide comes before the loop slide.** The reframe — *it was
   deciding not to speak the whole time* — is what makes someone want the
   mechanism. Lead with the loop and you get a polite nod; lead with the
   silence and you get the question.

And one addition: a **"what was built today"** slide near the end. It is a
hackathon rule, a judge will ask, and answering it before being asked reads
as confidence rather than defence.

---

## 1 — Title

> # Ora
> ### one circle, one loop

*Under it, small:* an agent that lives in a group chat and mostly decides
not to speak.

**Say:** nothing. Let them read it. Go.

---

## 2 — The thing that keeps happening

> - "Wait, did we ever decide a time for Saturday?"
> - A question gets asked, three people talk over it, it scrolls away
> - The plan changes in one app and not the other
> - "I'll check and get back to you" — nobody ever does

**Say:** "This isn't a productivity problem. Nobody's installing a project
tool for their friends. It's just what group chats are like."

*(15 seconds. Do not linger — everyone in the room already knows this.)*

---

## 3 — One line

> ## Most assistants belong to a person.
> ## **Ora belongs to a circle.**

*Under it, small:* it handles the small pieces of context that friendships
normally lose.

**Say:** "It lives in our Signal group and our WhatsApp group. Same people,
one shared picture. Let me just show you."

---

## 4 — [ DEMO ]

No slide. Switch to the three panes.

Run the six steps from [`demo-plan.md`](demo-plan.md). Talk as little as
possible. The two beats that have to land are **step 4** (it speaks, nobody
typed) and **step 6** (the room is busy, it stays quiet).

*If you say one thing during the demo, say it at step 4:* **"Nobody
prompted that."**

---

## 5 — What you just watched it not do

> ## Silence is the normal outcome.
>
> `no_go` · `no_go` · `no_go`
>
> Every one of those is a judgement, with the reasoning call behind it.

**Say:** "It read every one of those messages and decided not to speak.
That's not the agent being off. That's the decision, written down. It
doesn't speak because a timer fired — it speaks because the conversation
reached a state where it has something useful to add."

*This is the most important slide in the deck. Give it 30 seconds.*

---

## 6 — The loop

> Diagram 1 from [`architecture-visual.md`](architecture-visual.md).
>
> **PEOPLE → OBSERVE → ORIENT → DECIDE → ACT → PEOPLE**

*Caption:* nobody is at the start of that arrow.

**Say:** "Four stages, and it never stops. Messages come in. It updates
where the room stands and what's still open. It asks whether there's an
occasion. Almost always there isn't."

---

## 7 — Memory is the medium

> **Written at Observe and Act. Read at Orient and Decide.**
>
> Every reasoning turn is fresh. There is no long-lived session anywhere.
>
> ## The model is disposable; the cognition is not.

**Say:** "Nothing is carried inside a model session, because there isn't
one. Kill the process, swap the model — the group's context is untouched,
because it was never in the model. It's in the transcript, the position on
each room, the open notes, and the decision log."

*If asked why those are four things and not one:* the transcript is what
happened and can't be wrong; the standing paragraph is an interpretation
and can go stale, so it carries its age; notes are what's unresolved, each
with the condition that would close it. **Record is separate from
interpretation.**

---

## 8 — "Isn't that a heartbeat with memory?"

> ```
> heartbeat :  schedule ──▶ cognition
>
> Ora       :  cognition ──▶ intention ──▶ schedule ──▶ cognition
>                                              │
>                                 re-reads the room, may decline
> ```
>
> ## A heartbeat gives an agent another turn.
> ## Ora decides whether a turn should exist at all.

**Say:** "You saw this at step 5. The reminder was scheduled on Signal. The
plan changed on WhatsApp. It withdrew its own pending decision before it
ever fired. A timer can't do that — a timer doesn't know it made a
decision."

---

## 9 — What was built today

> **The inputs and the stores are reused.**
> **What decides was built today.**
>
> | reused | built today |
> |---|---|
> | the judging prompts (unedited, hashed) | the loop runtime |
> | the Signal / WhatsApp adapters | the gate and turn wiring |
> | the memory, search and routing clients | the proactive decide/act pair |
> | the schema shapes | orient — fold, notes, curation |
> | the evaluation cases | the tag path, the decision log, the brakes |
>
> `MANIFEST.md` — one row per file, appended as it was written.

**Say:** "Ora is a new repo, started this morning. Its building blocks come
from an earlier private project of mine, which isn't what I'm submitting.
Every file is one row in the manifest, marked reused or built-today, with
its source. The prompts I did not rewrite — they'd already been measured
against a 40-case suite, and an unmeasured rewrite this morning would have
been worse engineering, not more original. The cases ship with their runner
so you can check."

---

## 10 — Close

> ## It wasn't answering a prompt.
> ## It was following the life of the group.

Nothing else on the slide. Stop talking.

---

## The 90-second cut

If the slot is short, or the demo overran: **2 → 3 → [demo] → 5 → 10.**
Problem, one line, show it, the silence reframe, close. Slides 6–9 are all
answers to questions, and unasked questions don't need slides.

## The 3-slide cut

If you get a hallway pitch: **3 → [phone showing step 4] → 10.**

## Do not put on a slide

- a feature list
- a component diagram with more than six boxes
- any vendor or sponsor logo as if it were the product
- "cognition layer", "multi-agent orchestration", "revolutionary"
- anything from [`what-ships-today.md`](what-ships-today.md) §3 — it isn't
  built
