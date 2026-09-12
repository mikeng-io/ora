# Ora — one circle, one loop

> A heartbeat wakes a model. An ambient loop keeps the cognition between
> model calls. **The model is disposable; the cognition isn't.**

Ora lives in a circle's group chats — a real Signal group and a real
WhatsApp group, one workspace. When you call her, she answers. When
nobody calls her, she keeps a position on the room (Standing), tracks
what is unresolved (notes with closing conditions), and decides — with a
record of her own decisions — whether and when to speak.

Built for the AI Tinkerers "Agents, Everywhere" hackathon (Cyberport,
2026-09-12), distilled from [the reference project](https://github.com/mikeng-io)'s
ambient-loop bundle to the smallest scope that still closes the loop. Full
design: [`design/`](design/), starting at
[`design/00-overview.md`](design/00-overview.md).

## What judges is event-day; what she reads and sends is reused

The brief (`docs/ai-thinkathon-2026-09.md` §6) requires a net-new build
that may use existing "templates, reusable components, libraries, prompts,
starter code" as building blocks. Ora's line: **the inputs and the stores
are reused** — prompts (the reference project's measured instructions, copied unedited with
their hashes), the platform adapters, the Honcho and Exa clients, the
schema shapes, all marked `prefab` in [`MANIFEST.md`](MANIFEST.md) — **what
decides is ours, built on the day**: the loop runtime, every
decide/orient/tag function, the decision log's logic, the seams, the
brakes.

## The loop

```
                 THE CIRCLE  (Signal group + WhatsApp group = one workspace)
                      │
                      ▼
   OBSERVE   observe_signal.py / observe_whatsapp.py  →  messages
                      │
                      ▼
   ORIENT    orient.py     Standing (one paragraph per room) · notes (open, closing condition) · Honcho (people)
                      │
            ┌─────────┴──────────┐
            ▼                    ▼
   DECIDE   participation        proactive
            gate → turn          decide → act          ← both read `decisions`
            (window closes)      (tick over notes)
            └─────────┬──────────┘
                      ▼
   ACT       act.py   one send path, per-platform senders; every send lands back in messages
                      │
                      ▼
   OBSERVE AGAIN — and the TAG PATH (tag.py) feeds the loop: answer, then debrief → Standing + notes
```

Memory is the medium — written at Observe and Act, read at Orient and
Decide. Five Postgres tables and one Honcho workspace; no stage passes
state to the next through a model session. See
[`design/01-architecture.md`](design/01-architecture.md) for the full
schema, the five seams, and the brakes (fail-closed everywhere; silence is
the default; a `speak` without `grounded_on` becomes `hold`; deny-by-default
rooms on both sides).

## The demo (filled in after rehearsal #2)

| # | step | what happens | pass |
|---|---|---|---|
| 1 | ambient | A asks about tomorrow's meeting time on WhatsApp, untagged | Ora answers, grounded on a note she already held |
| 2 | tag | Mike tags her on Signal for the route | she answers with a real route + one web result |
| 3 | curation | — | the note she was holding closes, citing Mike's own reply |
| 4 | proactive | silence | she raises the one thing still open, once, in the home room |
| 5 | decide↔decide | A corrects on WhatsApp | she sees her own pending reminder — made on Signal — and drops it |
| 6 | silence | banter on both apps | nothing — the model judged, not crashed |

*Table to be filled from rehearsal #2's actual ids — never from intent
(`design/04 §3`). See [`RUNBOOK.md`](RUNBOOK.md) for the exact messages,
timings, and queries.*

**Disclosed, not a trick:** two notes are seeded before the run so step 1
has something to answer with honestly (`design/04 §1`) — "she speaks when
she holds something; today she held this." The video says so in its first
line.

## Why it scores

| criterion | what the judge sees |
|---|---|
| core functionality | a real Signal group and a real WhatsApp group, six scripted steps, each with a row or a trace to point at |
| innovation & theme | a chatbox cannot stay quiet for an hour and then speak because a note came due, and cannot see that the same people are talking in two apps |
| technical execution | fail-closed gate · a decision log with no model reasoning in it · her words can never become a note · every model call traced · one allowlist per side so one account can serve two agents |
| usefulness | the group gets an answer without anyone tagging; a dropped thread gets raised once and not again; a settled plan becomes a calendar event |

## Running it

```
uv sync
cp .env.example .env   # fill in the keys
# bring up postgres, signal-cli, whatsapp-bridge, honcho — see docker-compose.yml
python -m loop.loop     # event-day
python -m loop_tail     # the second screen
```

Tests: `ruff check . && pytest -q`. `tests/test_journey.py`'s six
`xfail(strict=True)` are the event-day scoreboard.

## What Ora is not

Not the reference project. No media, music, presence, skills, MCP tools, Discord, the
Dreamer runtime, or the reference project's config surface — one workspace, two rooms, one
loop. Not a prompt-engineering exercise: the relevance-gate instruction is
the reference project's measured v5, copied unedited, and the 12 gate cases ship with a
runner (`cases/run_gate.py`) so anyone who wants to edit it can measure it
first. Not a heartbeat — proactive runs on a tick, but what makes Ora an
ambient loop is what is serialised *between* wakes, not the trigger.

## Provenance

Every file is one line in [`MANIFEST.md`](MANIFEST.md): path, prefab or
event-day, source (for prefab), sha, who, when — the eligibility evidence
for a judge who asks which parts were built during the hackathon.
