# Video storyboard — the two-minute demo

**Do not cut together
phone recordings.** One Mac screen recording, one take of the six steps,
voice-over live.

## Layout on the Mac

- **Signal Desktop** — left third
- **WhatsApp Web** — middle third
- **`loop_tail` terminal** — right third

Phones are senders only and never appear on screen.

## Recording

QuickTime (⌘⇧5, full screen, microphone on) or OBS. Two takes; keep the
better. Editing is QuickTime trim only, plus a 10-second title card at the
start and the diagram at the end (two PNGs, dropped in).

**The clocks are the edit.** With the spec's compressed values the
six steps take real time on the order of ~180s (re-timed after the Opus
audit's finding 23 — the original ~90s estimate did not survive contact
with `settle_seconds` + gate + turn latency); that duration is why the
clocks are compressed, and why they are not tunable on the day.

## Storyboard

| t | frame | voice |
|---|---|---|
| 0–10 | title card over the two chats at rest | «A heartbeat wakes a model. Ora keeps the cognition between model calls. She speaks as our own account — no bot badge — and we seeded one open note before this run.» |
| 10–35 | step 1: the WhatsApp question; Ora answers; `loop_tail` shows `participation / spoke` | «Nobody called her. She had a note open on this, so she answered.» |
| 35–95 | steps 2–3: the tag on Signal fixes the time; the route answer; Standing rewrites; N1 closes citing Mike's row | «When called, she answers — and the answer closed the thing she was tracking.» |
| 95–130 | step 4: silence; the reminder appears in Signal at the scheduled minute | «Later — compressed here to seconds — she raises the one thing still open, once, in the home room.» |
| 130–160 | step 5: the WhatsApp correction; the revisit reminder flips to `cancelled` | «The correction came on the other app. She saw her own pending decision — made on Signal — and dropped it. No second raise.» |
| 160–180 | step 6 banter with `no_go` ×3, then the diagram | «Silence is the default. The model is disposable; the cognition isn't.» |

## PNGs needed before 14:00 (Opus audit finding — assigned, no owner in v1)

- **Title card** — "Ora — one circle, one loop", the tagline underneath.
- **Diagram** — the loop diagram from `README.md`,
  rendered as a clean image for the closing frame.

Owner: teammate B, due by 13:00 rehearsal #2 so both are ready before the
14:15 record block.

## Social post

The 20 seconds of step 5 plus the closing sentence, and the repo link.
Tag OpenAI, CopilotKit, OpenRouter. See `docs/social-post.md`.
