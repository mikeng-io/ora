# What ships today — the honest split

This is the single source of truth for "how much of this actually works?"
Every other document in `docs/` defers to this one. If a claim is not on
this page, do not make it on stage, in the video, or in the submission.

**Snapshot taken 2026-09-12 11:43 HKT.** It will be stale within the hour —
re-derive it before the 14:15 record block and before the 15:30
show-and-tell:

```sh
ls loop/                                   # which stages exist
uv run ruff check . && uv run pytest -q    # note how many journey markers are still xfail
python -m loop_tail                        # the live rows, if the stack is up
```

At the snapshot: `199 passed, 9 skipped, **6 xfailed**` — all six journey
markers still `xfail`, meaning **no demo step has been proved end to end
yet.** That number is the fastest honest answer to "how much works?"

---

## 1. Verified live, by hand, today

Each of these was exercised against the real thing, not a mock.

| capability | how it was verified |
|---|---|
| **Signal, live** | a linked-device `signal-cli` on a real account, in a real group |
| **WhatsApp, live** | a paired bridge on a real account, in a real group |
| **Observe path, end to end** | a real WhatsApp message landed as a `messages` row |
| **Postgres + pgvector** | running locally in Docker; `schema.sql` applied idempotently at start |
| **Honcho, self-hosted** | running locally in Docker against that Postgres |
| **Room allowlist, deny-by-default** | test reddens when the allowlist line is removed |
| **Own-send / sync drop** | its own outbound message, syncing back from the other device, is dropped at Observe — proved by reverting the line |
| **Image comprehension** | an image is stored content-addressed by sha256 and described by a vision model |
| **Exa web search** | live-tested |
| **Google Routes** | live-tested |
| **Model tracing** | every model-written row points at its `model_calls` row: stage, prompt digest, tokens, latency, finish reason |
| **Gate log** | one `gate_log` row per judged window, with verdict, score and the call behind it |
| **Decision log** | `decisions` rows with four writers, a closed verdict set, and **no model prose** |

### The image path, precisely

Worth stating exactly, because it is easy to overclaim:

- The bytes are stored once, keyed by sha256, on local disk.
- A vision model produces three fields — what the thing **is**, what it
  **shows**, and any legible **text** in it.
- The transcript the decision-making model reads carries a rendered marker:
  `[image abc123: title — description]`.
- The durable message row keeps the bare `[image abc123]` placeholder. The
  description is joined in at render time, not baked into the record.
- **Zero raw image bytes ever reach the decision-making model.** It reasons
  over a text description, never over pixels.
- All three fields empty is an index entry, not a claim of nothing. A
  failed description never blocks the image being stored or the message
  landing.

### Models

| role | model | endpoint |
|---|---|---|
| every decision — gate, turn, tag, fold, notes, curation, proactive | `deepseek-v4-flash`, `reasoning_effort` `none` or `low` | Ollama Cloud |
| image description only | a vision flash model | the **same** Ollama Cloud endpoint |

One endpoint. One decision model. The vision model is a separate model for
a non-judging task and is named as such — do not say "one model" without
that footnote.

---

## 2. Landing during the build window

Being written right now by parallel agents. Expected green before the
deadline; **not proven as of the snapshot above.**

| piece | what it does | state at 11:43 |
|---|---|---|
| `loop/decide_gate.py` | the relevance gate: is this window worth attention? | landed, unit-tested, **not run live** |
| `loop/decide_turn.py` | the participation turn: speak grounded / hold / cancel | landed, unit-tested, **not run live** |
| `loop/tag.py` | the tag path: answer, then debrief | landed, unit-tested, **not run live** |
| `loop/decide_proactive.py` | the proactive decide/act pair | landed, unit-tested, **not run live** |
| `loop/orient.py` | standing fold, note extraction, curation | landed, unit-tested, **not run live** |
| `loop/loop.py` | the runtime that drives all of the above | landed, **not run live**; lint not clean at snapshot |

"Unit-tested" means against a fake model client. It is evidence the wiring
holds; it is **not** evidence the demo works. Only a green journey marker,
or a row you can point at in `loop_tail`, is that.

Consequence for the demo: **steps 1, 2, 3, 4, 5 and 6 all depend on this
column.** The live demo is genuinely at risk in a way the shipped column is
not. `docs/demo-plan.md` carries a fallback per step, and
`tests/test_journey.py` — six `xfail(strict=True)` markers, one per demo
step — is the scoreboard. A marker still `xfail` means that step has not
been proved.

---

## 3. Not built, and must never be implied

An earlier private project by the same author has these. **Ora does not.**
Saying otherwise on stage is the one unrecoverable mistake.

- ❌ Discord
- ❌ Location, presence, or any life-tracking integration
- ❌ Local / on-device model inference
- ❌ Voice
- ❌ Music, skills, MCP tools
- ❌ Video comprehension (images only)

**Ora's inference is Ollama Cloud.** "Runs entirely offline" and "runs
locally" are **false for Ora** and must not appear anywhere. What *is* true
and worth saying: the group's data — every message, note, standing
paragraph and decision — lives in Postgres on a machine the group owns.
Only the text sent for a given judgement leaves it.

The calendar integration is a documented stub. It is a stretch goal,
attempted only after the six steps are green, and it is fine for it not to
happen.

---

## 4. Reused vs built today (hackathon eligibility)

The rule: *a pre-existing project may not be resubmitted, or extended and
entered as a new hackathon project*, and the team must be able to explain
which parts were created during the hackathon.

Ora is a new repository. Its building blocks come from an earlier private
project by the same author, which is not submitted and is not extended.
The line, said the same way every time:

> **The inputs and the stores are reused. What decides was built today.**

| reused (`prefab`) | built on the day (`event-day`) |
|---|---|
| the judging prompts — copied unedited, each with its sha | the loop runtime |
| the Signal and WhatsApp adapters | the gate and turn wiring, and their brakes |
| the Honcho, Exa and Routes clients | the proactive decide/act pair |
| the schema shapes | the orient stage — fold, notes, curation |
| the evaluation cases and their runner | the tag path |
| | the decision log's logic: who may write, what may be rendered |

`MANIFEST.md` is the row-by-row evidence: path, `prefab` or `event-day`,
source, sha, who, when — appended as each file was created, never
reconstructed afterwards.

**On the prompts, say this plainly if asked:** they judge, they were not
written today, and they are listed as reused. They had already been
measured against a 40-case suite. Rewriting them unmeasured on the morning
of a demo would have been worse engineering, not more original work. The
12-case subset ships with its runner so any edit can be measured before it
is believed.

---

## 5. Rules for every other document

1. No claim that is not in §1 may be stated in the present indicative.
2. Anything from §2 is described as "the demo will show", never "Ora does",
   until its journey marker is green.
3. Nothing in §3 appears in outbound material — the video, the slides, the
   submission text, the social post. Not to contrast, not to tease. The one
   exception is a **direct question** ("does it do voice?", "how much is
   real?"), where the answer is a plain no.
4. The seeded notes are disclosed in the first line of the video and on the
   first slide that shows them. They are not a trick, and hiding them would
   make them one.
