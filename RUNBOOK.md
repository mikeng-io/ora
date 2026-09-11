# RUNBOOK — the day, the seed, the six steps

This is the script the rehearsal and the live demo both run against
(design/04, design/05 §1). IDs in the query columns are blank until
rehearsal #2 fills them in — the README's demo table is written from
rehearsal #2's ids, never from intent (design/04 §3).

## §0. Seeding — before the run starts

Both notes are created *before* the run, visibly, and disclosed on stage
(the video's first line, design/05 §3): "we seeded one open note before
the run." This is what makes step 1 an honest `-known` case rather than an
emergent one (design/04 §1, design/06 "what would show this bundle was
wrong").

| note | title | closing condition | anchor | room |
|---|---|---|---|---|
| N1 | Cyberport tomorrow — meeting time not fixed | a time is agreed | 2026-09-13 10:00, Cyberport | Signal |
| N2 | Dinner after Cyberport — where? | a place is agreed | 2026-09-13 19:00 | Signal |

Seed with a direct insert (event-day, once `loop/store.py` is live):

```sql
INSERT INTO note (id, workspace, room_label, title, closing_condition, anchor_at, anchor_place, created_at)
VALUES
  ('N1', 'demo', 'Signal', 'Cyberport tomorrow — meeting time not fixed', 'a time is agreed', '2026-09-13 10:00+08', 'Cyberport', now() - interval '10 minutes'),
  ('N2', 'demo', 'Signal', 'Dinner after Cyberport — where?', 'a place is agreed', '2026-09-13 19:00+08', NULL, now() - interval '10 minutes');
```

Confirm both are visible in `loop_tail` before step 1 starts.

## Cast

- **Ora** — the linked device on each account, speaking *as* the account.
- **Mike** — presenter; tags Ora in step 2.
- **Teammate A** — "the group": asks in step 1, corrects in step 5, banters
  in step 6. Two phones, both apps, both rooms joined.

Rooms: `Signal` (home room) and `WhatsApp`, workspace `demo`. Clocks are
`design/01 §5`'s compressed values — do not touch them after rehearsal #1
(ORA-11).

## The six steps

Times are minutes:seconds from the run's own start (`t0`), not wall clock.

### Step 1 — t 0:00 — ambient (gate → turn)

**A, on WhatsApp:** `聽日幾點去 Cyberport？`

Nobody tags Ora. Pass: `gate_log` row `go` for the WhatsApp window; Ora
answers on WhatsApp; `decisions` row `participation / spoke` with
`grounded_on = 'note:N1'`.

```sql
SELECT verdict, score FROM gate_log WHERE conversation_id = :whatsapp_id AND ts > :t0;
SELECT writer, verdict, grounded_on FROM decisions
  WHERE workspace = 'demo' AND conversation_id = :whatsapp_id AND writer = 'participation'
  ORDER BY ts DESC LIMIT 1;
```

`gate_log.id = ____`  `decisions.id = ____`

### Step 2 — t 0:40 — tag path

**Mike, on Signal:** `@ora 咁聽日 10 點集合，點去？`

Pass: Ora answers with a route (Directions) + one Exa result; `standing`
for Signal rewritten (`updated_at > t0`); `decisions` row `tag / replied`.

```sql
SELECT body, updated_at FROM standing WHERE conversation_id = :signal_id;
SELECT verdict FROM decisions WHERE writer = 'tag' AND ts > :t0 ORDER BY ts DESC LIMIT 1;
```

`decisions.id = ____`  `messages.id (her reply) = ____`

### Step 3 — t 1:00 — orient: curation

Silent step — Mike's tag at step 2 fixed the time. Pass: N1
`retired_reason = 'closed'`, `cited_row_id` = Mike's row from step 2 (a
**human** row — the honest close, ORA-8); N2 still open.

```sql
SELECT id, retired_reason, cited_row_id FROM note WHERE id = 'N1';
SELECT id FROM messages WHERE id = (SELECT cited_row_id FROM note WHERE id = 'N1') AND is_ora = FALSE;
```

### Step 4 — t 1:00–2:30 — ambient: proactive

Silence. Pass: `reminders` row R1 `pending` for N2 → at `due_at` Ora
speaks in **Signal** (home room), `「晚飯食邊度？」`; `decisions` row
`proactive_act / spoke`; unanswered, the act turn sets `note.strikes = 1`
on N2 and schedules **R2** (revisit, +90s) `pending`.

```sql
SELECT id, state, due_at FROM reminders WHERE note_id = 'N2' ORDER BY created_at;
SELECT writer, verdict FROM decisions WHERE writer LIKE 'proactive%' AND ts > :t0;
```

`reminders R1.id = ____`  `reminders R2.id = ____`

### Step 5 — t 2:40 — decide ↔ decide

**A, on WhatsApp:** `唔使諗啦，聽日唔食飯，改咗下星期`

Pass: the WhatsApp participation turn sees **R2** (a Signal row) in
`<loop_decisions>` and cancels it — `reminders.R2.state = 'cancelled'`,
`decisions` row `participation / cancelled` with `reminder_id = R2`;
curation marks N2 `moot`; **no second raise in Signal.**

```sql
SELECT state FROM reminders WHERE id = 'R2';
SELECT verdict, reminder_id FROM decisions WHERE verdict = 'cancelled';
SELECT count(*) FROM messages WHERE conversation_id = :signal_id AND is_ora = TRUE AND ts > :t_step5;  -- expect 0
```

### Step 6 — t 3:30 — silence

**A, on both apps:** banter, a gif link, `lol` ×3 — after the 60s cooldown
has expired, so silence is the gate's, not the cooldown's.

Pass: `gate_log` rows `no_go` for every window, `finish_reason = 'stop'`
(not an error — a dead model produces `error_kind`, never a real `no_go`);
zero `is_ora` rows after `t_step6`.

```sql
SELECT verdict FROM gate_log WHERE ts > :t_step6;  -- expect all no_go
SELECT count(*) FROM messages WHERE is_ora = TRUE AND ts > :t_step6;  -- expect 0
```

## Stretch (only after 1–6 are green)

On step 3, `loop/calendar.py` creates one Ambiguous event from N1's
anchor; step 4's text says `「聽日 10 點已經入咗 calendar」`.
`GET /api/calendar/events` should show one event. Attempted only after
1–6 are all green (design/06 ORA-12) — the request body is still a
documented TODO (`loop/calendar.py`).

## Error table (design/04 §4)

| what breaks | when we know | what we do |
|---|---|---|
| whatsapp-bridge unstable | 10:30 smoke | Signal-only demo; cross-platform claim moves to the README; steps 1 and 5 happen on Signal |
| Ollama Cloud slow / erroring | rehearsal #1 | gate fail-closed → silence, not nonsense; `tests/test_journey.py` is the fallback for the step that died |
| step 1 silent | rehearsal #1 | check N1 rendered; if still holding, step 1 becomes a tag |
| step 1 speaks ungrounded | rehearsal #1 | the grounding downgrade fired — that is a brake demo, say so |
| proactive never fires | rehearsal #1 | check clocks in `.env`; `loop_tail` shows no `pending` row → read the decide turn's reason code |
| step 5 double-raise | rehearsal #1 | `<loop_decisions>` not workspace-scoped or R2 not scheduled — `test_journey.py` step 5 covers all three |
| mention goes out as plain text | 10:30 first send | `people.toml` ids; plain text is the designed fallback, not a failure |
| Signal linked device not receiving | 10:00 | re-link from the phone; WhatsApp-only smoke until then |
| 13:15 and steps 4/5 not green | cut line | video shows 1–3 + 6 live, 4–5 from `tests/test_journey.py`; calendar not attempted |
