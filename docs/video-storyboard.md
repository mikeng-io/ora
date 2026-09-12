# Video storyboard — the two minutes

## The one job

A viewer must not reach the end thinking *"impressive chatbot."*

They should reach the end thinking:

> **"Wait — it wasn't being prompted. It was following the life of the
> group."**

Every choice below serves that. If a shot does not move a viewer toward
that sentence, cut it.

The two moments that do the work are **step 4** (it speaks with nobody
having typed) and **step 6** (the room is busy and it stays quiet on
purpose). If the edit has to lose time, lose it from steps 2 and 3, never
from those two.

---

## Rules for the shoot

**Do not cut together phone recordings.** One Mac screen recording, one
continuous take of the six steps, voice-over live.

**Layout on the Mac:**

| left third | middle third | right third |
|---|---|---|
| Signal Desktop | WhatsApp Web | `loop_tail` terminal |

Phones are senders only and never appear on screen.

**Before rolling:** scroll both chat windows so only the demo run will be
visible. No real chat history in frame. Notifications off, both apps and
the OS.

**Record:** QuickTime (⌘⇧5, full screen, microphone on) or OBS. Two takes;
keep the better one. Editing is trims only, plus a title card at the front
and the diagram at the back (two PNGs, dropped in).

---

## The source take runs long. That is fine.

With the compressed clocks the six steps take **~180 seconds** of real
time — re-timed after the audit found the original 90-second estimate did
not survive contact with settle time plus gate plus turn latency. The
submission video is **120 seconds**. So the edit is a trim, and here is
where the 60 seconds comes out:

| trim | where | how much | why it's safe |
|---|---|---|---|
| T1 | the settle wait before step 1's gate line | ~8s | nothing is on screen but a cursor |
| T2 | between step 2's reply and step 3's curation line | ~10s | dead air between two `loop_tail` lines |
| T3 | **inside step 4's silence** | ~30s | keep ~8 seconds of genuine quiet; cut the middle |
| T4 | between step 5 and step 6's banter | ~12s | waiting out the cooldown |

**On T3, keep the `loop_tail` timestamps legible on both sides of the
cut.** The viewer should be able to see that time passed. A silence that
was edited to look shorter is fine; a silence edited to look like it never
happened is not, and the timestamps are what makes the difference visible.

Do not trim inside a `loop_tail` line as it prints. Cut on quiet frames.

---

## Scene by scene

| t | on screen | what you hear | what Ora is doing | why it matters |
|---|---|---|---|---|
| **0:00–0:08** | Title card: *Ora — one circle, one loop*. Fades to the three-pane layout, both chats at rest | «Most assistants belong to a person. This one belongs to a group chat. It speaks as our own account — no bot — and we seeded two open notes before this run.» | nothing | frames the whole thing, and discloses the seed in the first eight seconds |
| **0:08–0:30** | **Step 1.** A message arrives in WhatsApp. Beat. `loop_tail`: `GATE · WhatsApp · go`, then `TURN · spoke · grounded note:N1`. The reply appears in WhatsApp | «Nobody called it. But it was already holding a note about tomorrow — so it answered.» | judged the window worth a contribution, answered grounded on a note it held | the first surprise: an untagged answer, and a visible reason for it |
| **0:30–0:52** | **Steps 2–3.** Mike tags it on Signal. A reply with a route. `loop_tail`: `FOLD · standing rewritten`, then `CURATION · N1 closed · cites row 219 (Mike)` | «When it *is* called, it answers. And watch — that answer just closed the thing it was tracking. It's citing Mike's message, not its own.» | tag path answers; the debrief folds it into the room's position and closes N1 | an answer isn't the end of a conversation, it's a change in state — and the agent doesn't mark its own homework |
| **0:52–1:18** | **Step 4.** Both chats still. `loop_tail`: `PROACTIVE · remind N2 → R1 due 12:03:30`. Hold on the quiet. Then 「晚飯食邊度？」 appears **in Signal** | *(let 3–4 seconds run silent, then)* «Nobody typed. It decided, earlier, that one thing was still open — and it chose when that should matter.» | formed an intention, scheduled it, re-read the room at fire time, spoke once in the home room | **the money shot.** This is the beat that separates "chatbot" from "following the group" |
| **1:18–1:42** | **Step 5.** A correction arrives in **WhatsApp**. `loop_tail`: `TURN · WhatsApp · cancel R2 (saw it in loop_decisions)`. R2 flips to `cancelled`. Cursor moves to the **Signal** pane and rests there — nothing appears | «The plan changed on the other app. It saw its own pending reminder — the one it had made over on Signal — and dropped it. No second raise.» | one decider read another decider's pending decision, across rooms, and withdrew it | the thing a chatbox structurally cannot do — and the thing that stops an ambient agent becoming a nuisance |
| **1:42–1:55** | **Step 6.** Banter fills both chats. `loop_tail`: `no_go`, `no_go`, `no_go`. Nothing sent | «It's reading all of this. And deciding not to speak. Silence isn't the agent being off — it's the decision, in a row.» | the gate judged three windows not worth contributing to | reframes everything the viewer just watched: the speaking was selected, not triggered |
| **1:55–2:00** | Cut to the loop diagram, held still | «The model is disposable. The cognition isn't.» | — | the architectural claim, earned last |

---

## The voice-over, as one script

Read at a normal speaking pace. Do not rush the pause at 0:57.

> Most assistants belong to a person. This one belongs to a group chat.
> It speaks as our own account — no bot — and we seeded two open notes
> before this run.
>
> Nobody called it. But it was already holding a note about tomorrow —
> so it answered.
>
> When it *is* called, it answers. And watch — that answer just closed the
> thing it was tracking. It's citing Mike's message, not its own.
>
> *(pause, 3–4 seconds of quiet)*
>
> Nobody typed. It decided, earlier, that one thing was still open — and it
> chose when that should matter.
>
> The plan changed on the other app. It saw its own pending reminder — the
> one it had made over on Signal — and dropped it. No second raise.
>
> It's reading all of this. And deciding not to speak. Silence isn't the
> agent being off — it's the decision, in a row.
>
> The model is disposable. The cognition isn't.

---

## Cards

**Title card (0:00):**

```
Ora
one circle, one loop

an agent that lives in a group chat
and mostly decides not to speak
```

**End card (1:55):** diagram 1 from
[`docs/architecture-visual.md`](architecture-visual.md), held still, with
one line under it:

```
It wasn't answering a prompt.
It was following the life of the group.
```

Both PNGs at 1920×1080. **Owner: teammate B, due 13:00** — before
rehearsal #2, so nothing is being rendered during the record block.

---

## Do not say

- "cognition layer", "multi-agent orchestration", "context engineering" —
  none of these mean anything to someone watching a group chat
- "revolutionary", "world's first", "the future of"
- any framework, vendor or sponsor name as if it were the product
- "runs locally" / "fully offline" — **false for Ora**, the inference is
  Ollama Cloud (see [`what-ships-today.md`](what-ships-today.md) §3)
- a feature list. Nobody watching a two-minute video wants an inventory
- anything from `what-ships-today.md` §3 — not even to contrast against

## If a step failed in both takes

Do not re-shoot around it and do not narrate a step that did not happen.
Cut that step out of the video entirely and let the remaining ones carry
it — steps 4 and 6 alone still make the argument. Then say in the
submission description which step is shown from the journey test instead
of live. A two-minute video with five honest steps beats a perfect one with
a lie in it.

---

## Downstream

The social post takes the 20 seconds of step 5 (1:18–1:42) plus the closing
line and the repo link. See [`social-post.md`](social-post.md).
