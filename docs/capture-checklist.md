# Capture checklist

What to actually record, in what order, and how to know you got it.

**The governing rule for log footage:**

> Film the terminal only where it proves something the chat cannot show.

Chat windows prove that a message arrived. They cannot prove that a silence
was a *decision*, that an answer was grounded in a note, or that a pending
reminder was withdrawn. Those need the log. Everything else the terminal
does is decoration, and a viewer who is watching scrolling JSON is not
watching the group chat.

---

## A. Before you press record

### The machine

- [ ] **Do Not Disturb on**, macOS and both chat apps. One notification
      banner ruins a take.
- [ ] Close every other app. No dock badges, no browser tabs with anything
      identifiable in the title.
- [ ] Desktop wallpaper plain. Menu bar clean.
- [ ] Display at **1920×1080** (or record full screen and export at 1080p).
      Do not record a 5K screen and downscale — the terminal text will mush.

### The chat windows

- [ ] **Scroll both chats so only the demo run will be visible.** No real
      history in frame. This is the single most important prep step and the
      easiest to forget.
- [ ] Avatars and display names: fine if they're the team's. Nothing else.
- [ ] Font size up one or two notches in both apps. A judge is watching this
      on a laptop, possibly a phone.
- [ ] Signal Desktop left third, WhatsApp Web middle third, terminal right
      third. Windows sized so no pane is clipped.

### The terminal

- [ ] `python -m loop_tail`, full width of its third.
- [ ] **Dark background, high contrast, large font.** Test it: screenshot
      the terminal, view at 50%, read a `no_go` line. If you can't, go
      bigger.
- [ ] Scrollback cleared. The first line in the take should be from this run.
- [ ] Confirm third-party loggers are quiet — one fixed-grammar line per
      event and nothing else. If library chatter appears, stop and fix it;
      it will make the whole system look unfinished.

### The state

- [ ] `docker compose ps` — all four services up.
- [ ] N1 and N2 seeded, `created_at` visibly before the run, both showing
      open in `loop_tail`.
- [ ] `uv run pytest -q` — note which journey markers are still `xfail`.
      Those are the steps you cannot claim.
- [ ] Both phones in hand, both apps open on the right group, messages
      pre-typed but **not sent** — so each one is a single tap on cue.

---

## B. The primary asset: one continuous take

**Asset 1 — the full run.** One screen recording, microphone on, all three
panes, from before step 1 to after step 6. ~180 seconds. Voice-over live.

Record **two of these**. Keep the better one. This single file is the
video; everything else on this page is either a backup or a still.

Do not record the steps separately and assemble them. A cut between steps
is a place where a viewer can reasonably wonder what was removed, and the
one thing this demo is selling is that it wasn't being driven.

---

## C. Shot list

| # | shot | source | ~len | proves | priority |
|---|---|---|---|---|---|
| 1 | full six-step run, three panes, voice-over | screen rec | 180s | everything | **must** |
| 2 | full six-step run, second take | screen rec | 180s | insurance | **must** |
| 3 | terminal only, step 4's `PROACTIVE … remind N2 → R1 due …` line appearing while both chats sit still | crop of #1 | 6s | the intention existed **before** the message | **must** |
| 4 | terminal only, step 6's three `no_go` lines while the chats scroll | crop of #1 | 8s | silence was a decision, not absence | **must** |
| 5 | terminal only, step 5's `TURN · WhatsApp · cancel R2 (saw it in loop_decisions)` + the reminders row flipping to `cancelled` | crop of #1 | 8s | one decider withdrew another's pending decision, across apps | **must** |
| 6 | terminal only, step 1's `TURN · spoke · grounded note:N1` | crop of #1 | 5s | the answer was grounded in held state, not improvised | should |
| 7 | terminal only, step 3's `CURATION · N1 closed · cites row 219 (Mike)` | crop of #1 | 5s | it closed a note citing a **human** row | should |
| 8 | Signal pane held still for ~6s after step 5, nothing arriving | crop of #1 | 6s | the no-second-raise claim is visible as *nothing happening* | should |
| 9 | `loop_tail`'s notes/reminders tables with N1 closed and N2 moot | still or 5s | 5s | end state, for a slide | nice |
| 10 | `uv run pytest -q` passing, journey markers visible | separate rec | 10s | the fallback exists and is green | nice |
| 11 | phone-in-hand sending a message | **do not shoot** | — | — | ❌ |

Shots 3–8 are **crops of shot 1**, not separate recordings. Do not re-stage
them — a re-staged log line is a fabricated log line, and you would then be
filming a claim rather than an event.

---

## D. What only the log can prove

This is the table to consult when deciding whether a terminal shot earns
its screen time.

| step | the chat shows | the chat **cannot** show | so film the log? |
|---|---|---|---|
| 1 | an answer appeared without a tag | that it was grounded in note N1, and that a gate said `go` first | **yes** — briefly |
| 2 | a reply with a route | that standing was rewritten behind it | no — the reply is the story |
| 3 | *nothing* | that N1 closed, citing a human's row | **yes** — this step is invisible otherwise |
| 4 | a message arrives from nowhere | that an intention was formed and scheduled *earlier* | **yes** — the `remind … due …` line is the proof |
| 5 | a correction, then nothing in Signal | that a pending reminder was seen and cancelled | **yes** — the strongest log shot in the run |
| 6 | busy chat, no reply | that three windows were judged and declined | **yes** — silence is indistinguishable from absence without it |

Steps 3, 4, 5 and 6 are log-dependent. Step 2 is not. That ratio is roughly
right: the terminal is on screen the whole time as ambient texture, but the
viewer's attention is pulled to it four times, not seven.

---

## E. Stills and diagrams

| asset | spec | for | owner | due |
|---|---|---|---|---|
| Title card | 1920×1080 PNG, "Ora — one circle, one loop" + tagline | video 0:00, slide 1 | teammate B | 13:00 |
| Loop diagram | 1920×1080 PNG, diagram 1 from [`architecture-visual.md`](architecture-visual.md) | video end card, slide 6 | teammate B | 13:00 |
| Heartbeat-vs-Ora two-liner | plain text on a card, or drawn live | slide 8, only if asked | Mike | — |
| End-state screenshot | `loop_tail` tables, N1 closed / N2 moot | slide 9, social post | from shot 9 | after rehearsal #2 |

**Both PNGs must exist before the 14:15 record block.** Rendering a diagram
during the record window is how the record window disappears.

Check every PNG at 25% zoom. If `OBSERVE / ORIENT / DECIDE / ACT / silence`
aren't readable, the type is too small.

---

## F. After the take, before you upload

- [ ] Watch the whole thing once with **sound off**. Does step 4 still read
      as "nobody typed"? If not, the pause was trimmed too hard.
- [ ] Watch it once at **phone size**. Can you read the `no_go` lines?
- [ ] Scrub for anything identifiable: phone numbers, real names beyond the
      team, notification banners, a stray browser tab, a visible file path
      with something private in it.
- [ ] Confirm **no step is narrated that did not happen.** If a step failed
      in both takes, cut it out — do not describe it over footage of
      something else.
- [ ] Confirm the seed disclosure is audible in the first ten seconds.
- [ ] Confirm nothing in
      [`what-ships-today.md`](what-ships-today.md) §3 is claimed, shown, or
      implied — especially "runs locally".
- [ ] Length ≤ 2:00.

---

## G. Downstream assets

- **Social post clip:** shot 5 plus the surrounding chat context, ~20s,
  with the closing line. See [`social-post.md`](social-post.md).
- **Submission description:** ≤200 words from
  [`narrative.md`](narrative.md) §1–3 plus the reused/built-today line from
  [`what-ships-today.md`](what-ships-today.md) §4.
- **Live show-and-tell:** the same three panes, live, no recording. See
  [`demo-plan.md`](demo-plan.md) for the failure protocol.
