# The diagram

One diagram has to land in about five seconds, from the back of a room, on
a slide nobody will pause. That is diagram 1. Everything else on this page
is for the person who leans in afterwards and asks a second question.

---

## 1. The five-second diagram

The only thing it has to say: **this is a circle, it starts and ends with
people, and most of the time the answer is silence.**

```mermaid
flowchart LR
  P(["THE CIRCLE<br/><small>Signal group · WhatsApp group<br/>the same four people</small>"])
  O["OBSERVE<br/><small>what was said</small>"]
  R["ORIENT<br/><small>where it stands now</small>"]
  D{"DECIDE<br/><small>is there an occasion?</small>"}
  A["ACT<br/><small>say it, once</small>"]
  S(["silence<br/><small>the normal outcome</small>"])

  P --> O --> R --> D
  D -->|"rarely"| A
  D -.->|"almost always"| S
  A --> P

  classDef people fill:#1f2937,stroke:#111827,color:#f9fafb
  classDef stage fill:#eef2ff,stroke:#4338ca,color:#1e1b4b
  classDef quiet fill:#f3f4f6,stroke:#9ca3af,color:#4b5563,stroke-dasharray:4 3
  class P people
  class O,R,D,A stage
  class S quiet
```

If the slide only gets one caption, it is this:

> **Nobody is at the start of that arrow.**

ASCII fallback, for a terminal or a README:

```
   THE CIRCLE ──▶ OBSERVE ──▶ ORIENT ──▶ DECIDE ──┬──▶ ACT ──┐
        ▲                                          │          │
        │                                          └┈┈▶ silence│
        └──────────────────────────────────────────────────────┘
```

---

## 2. Memory is the medium

Show this one only if someone asks how the loop survives between model
calls — which, when the demo has worked, they will.

The point of the picture is the direction of the arrows: **memory is
written at the edges and read in the middle.** There is no arrow from one
stage directly into another.

```mermaid
flowchart TB
  subgraph edges["writes"]
    O["OBSERVE"]
    A["ACT"]
  end

  M[("shared state<br/><small>transcript · notes · standing · decisions</small>")]

  subgraph middle["reads"]
    R["ORIENT"]
    D["DECIDE"]
  end

  O -->|"the message that arrived"| M
  A -->|"the message it sent"| M
  M -->|"the room, with ages"| R
  M -->|"what's open, what it already decided"| D
  R -.->|"rewrites standing, opens/closes notes"| M
  D -.->|"logs the verdict"| M

  classDef store fill:#1f2937,stroke:#111827,color:#f9fafb
  class M store
```

The claim this diagram is making:

> **The model is disposable; the cognition is not.**

Every reasoning turn is fresh. Nothing is carried in a long-lived LLM
session, because there isn't one. Kill the process, swap the model — the
group's context is untouched, because it was never in the model.

---

## 3. The two backbones

Only for the deep-dive question "so how is the tag different from the
ambient path?"

```mermaid
flowchart LR
  subgraph tag["TAG PATH — someone called it"]
    direction LR
    T1["one tag"] --> T2["one fresh turn"] --> T3["one reply"]
  end

  subgraph amb["AMBIENT LOOP — nobody called it"]
    direction LR
    A1["state accumulates"] --> A2{"is there<br/>an occasion?"}
    A2 -->|"no"| A3(["nothing"])
    A2 -->|"yes"| A4["speak, grounded"]
  end

  tag -.->|"the answer becomes state"| amb

  classDef quiet fill:#f3f4f6,stroke:#9ca3af,color:#4b5563,stroke-dasharray:4 3
  class A3 quiet
```

| | tag path | ambient loop |
|---|---|---|
| trigger | a person | a state |
| scope | one room, one exchange | the whole circle, across time |
| question | "what's the answer?" | "is there an occasion?" |
| usual outcome | a reply | nothing |

The tag path feeds the ambient loop: once it has answered, it folds the
exchange back into the room's standing position and into what's still
open. An answer is not the end of a conversation; it is a change in state.

---

## 4. Proactivity, if the "isn't this a heartbeat?" question comes

Do not draw this unless asked. If asked, draw it small, by hand, in two
lines:

```
heartbeat :   schedule ──▶ cognition
Ora       :   cognition ──▶ intention ──▶ schedule ──▶ cognition
                                                          │
                                            re-reads the room, may decline
```

---

## Producing the PNGs

Two images are needed before the 14:15 record block:

1. **Title card** — "Ora — one circle, one loop", tagline underneath.
2. **Diagram 1**, rendered clean, for the closing frame of the video and
   the closing slide.

Render diagram 1 from this file (any Mermaid renderer, or paste into
mermaid.live), export at **1920 × 1080** on a white or very dark
background — it will be seen over a screen recording, so contrast matters
more than prettiness. Check it at 25% zoom: if the five words
OBSERVE / ORIENT / DECIDE / ACT / silence are not readable, the type is
too small.

**Owner: teammate B. Due 13:00**, before rehearsal #2, so both PNGs exist
before recording rather than during it.
