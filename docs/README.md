# docs/ — the submission package

> Most assistants belong to a person. **Ora belongs to a circle.** It lives
> in a small group's real chats, carries context across conversations and
> across time, and mostly decides not to speak.
>
> **It wasn't answering a prompt. It was following the life of the group.**

Built for the AI Tinkerers "Agents, Everywhere" hackathon, Hong Kong,
2026-09-12.

---

## Read in this order

| | document | what it's for | read it before |
|---|---|---|---|
| 1 | **[what-ships-today.md](what-ships-today.md)** | the honest shipped / in-flight / not-built split, and the reused-vs-built-today line | **everything else** |
| 2 | [narrative.md](narrative.md) | the story and the thesis, human problem first | writing or saying anything |
| 3 | [architecture-visual.md](architecture-visual.md) | the five-second diagram, plus two deeper ones | making slides or the end card |
| 4 | [demo-plan.md](demo-plan.md) | the six-step run, what proves each step, what to do when one fails on stage | rehearsal |
| 5 | [video-storyboard.md](video-storyboard.md) | the 2-minute video, scene by scene, with the trim plan | the 14:15 record block |
| 6 | [capture-checklist.md](capture-checklist.md) | exactly what to record, and which log shots earn their screen time | pressing record |
| 7 | [slides.md](slides.md) | ten slides, plus a 90-second and a 3-slide cut | show-and-tell |
| 8 | [judge-qa.md](judge-qa.md) | crisp answers to the twelve questions that will be asked | Q&A |
| 9 | [social-post.md](social-post.md) | the post, the clip, the tags | 16:00 |

Elsewhere in the repo: [`MANIFEST.md`](../MANIFEST.md) is the file-by-file
eligibility evidence, [`RUNBOOK.md`](../RUNBOOK.md) has the exact messages,
timings and verification queries, and [`cases/journey.yaml`](../cases/journey.yaml)
is the frozen demo script.

---

## The four sentences these documents are built on

1. **Ora handles the small pieces of context that friendships normally
   lose.**
2. **Memory is the medium — written at Observe and Act, read at Orient and
   Decide.**
3. **The model is disposable; the cognition is not.**
4. **A heartbeat gives an agent another turn. Ora decides whether a turn
   should exist at all.**

Say these the same way every time. Consistency across a video, a deck and
a Q&A is most of what "coherent" means to a judge.

---

## Three standing rules

**Never claim a capability that isn't in
[`what-ships-today.md`](what-ships-today.md) §1.** Anything in §2 is "the
demo will show", not "Ora does". Anything in §3 is not mentioned at all.

**Never say Ora runs locally or offline.** Its inference is Ollama Cloud.
The group's *data* is on the group's own machine — that is the true and
better claim.

**Disclose the seeded notes.** First line of the video, first time they
appear on stage. A demo that manufactures the agent's grounding and hides
it is a demo that lies; four seconds of disclosure buys the rest of the run.
