"""Ora's proactive half — two deciders, one file (`design/07` item, agent C's
share): `decide` (writer='proactive_decide') looks at the open notes once and
decides whether anything is worth raising, and when; `act`
(writer='proactive_act') fires when a reminder it set comes due, reads the
room again, and either speaks in the home room or stays quiet. Nobody asked
either turn anything — that is the whole point (`prompts/proactive.md`).

CMS, once: each turn interprets its inputs into a fixed model (the eligible
notes, the rendered blocks, the reminder about to fire) exactly once, reduces
the model call's answer to a decidable verdict, and serializes it as one
`decisions` row (+ a `reminders` row where relevant) with `call_id`
provenance. Fail closed, and here failing closed means silence: any transport
error, timeout, unparseable JSON, non-`stop` finish, or unrecognised shape
degrades to `decline` (decide) / `silent` (act) — never a speak.

**No second raise** (the one rule this module exists to hold, `design/06`):
a note the model has never raised, or raised and already had answered, is
eligible; a note it raised and got silence back is "struck" and held out of
the model's own view — not merely discouraged — until `note.revisit_at`
passes. The strike is counted the pass AFTER the silence is observed (in
`decide`, via `strike_unanswered_notes`), never by `act` itself, which has
no way yet to know whether anyone will answer.

Ambiguity in `prompts/proactive.md`, and what this module chose (owed to
whoever reads this later):

1. The prompt's answering contract is written against a multi-turn,
   tool-calling harness — `remind(when, text, note)` is a tool call made
   *during* the turn, and the closing `{"decisions": [...]}` is a separate,
   after-the-fact summary with no `when`/`text` in it. `ModelClient` here is
   single-shot JSON only (`loop/model.py`'s own docstring: "never a
   structured-output tool call"). So this module folds the `remind` call's
   arguments into the same JSON object: a `"remind"` entry MUST also carry
   `"when"` (ISO-8601 with an offset) and `"text"` (the intent), or it is
   treated as malformed and declined — never invented.
2. The prompt also asks for `about`/`note_read`/`ask_memory` reads before
   ever setting a reminder. This module has no tool-calling loop and was
   told to build only against `render_notes` / `render_standing` /
   `render_loop_decisions` / `render_transcript` — so those reads are simply
   not offered here; the model judges from the four rendered blocks alone.
   Nothing in the four required render functions exposes `strikes` or
   `revisit_at` either (`NoteEntry` has neither field), which is why the
   no-second-raise rule is enforced by *filtering what the model is shown*,
   never by asking it to read a count it cannot see.
3. The prompt's example answer uses a bracket index ("note": "1") for a
   reference project whose notes are numbered that way; Ora's notes carry
   their own id (`"N1"`, `"N2"`, `note.id`). This module treats `"note"` in
   the model's JSON as that id, since it is the only handle the model was
   ever given (the same id `render_notes` lists and `render_loop_decisions`
   cross-references).
4. There is no separate prompt file for the `act` half — `prompts/` is
   frozen and this deliverable is exactly two files. `act`'s system prompt
   is therefore a short instruction written here, not sourced from
   `prompts/`; it is deliberately narrow (compose-or-hold for one already-
   chosen intent), not a copy of the decide contract.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loop import act as act_module
from loop import decisions
from loop.config import Config, Room
from loop.people import PeopleDirectory
from loop.render import (
    DecisionEntry,
    NoteEntry,
    StandingEntry,
    TranscriptRow,
    render_loop_decisions,
    render_notes,
    render_standing,
    render_transcript,
)
from loop.store import Store

log = logging.getLogger("ora.proactive")

PROACTIVE_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "proactive.md"

DECIDE_STAGE = "proactive_decide"
ACT_STAGE = "proactive_act"

DEFAULT_REVISIT_AFTER = timedelta(hours=6)

# reason codes recorded on `decisions.reason` — short labels, never model
# prose (the model's own "why" is read for logging/debugging only and is
# never persisted into that column).
R_SCHEDULED = "scheduled"
R_DECLINED = "declined"
R_UNKNOWN_ACTION = "unknown_action"
R_UNKNOWN_NOTE = "unknown_note"
R_REMIND_MISSING_FIELDS = "remind_missing_fields"
R_MODEL_TIMEOUT = "model_timeout"
R_MODEL_ERROR = "model_error"
R_UNPARSEABLE = "unparseable_response"
R_DB_ERROR = "db_error"

R_SPOKEN = "delivered"
R_HELD = "held"
R_EMPTY_TEXT = "empty_text"
R_NO_GROUNDING = "no_note_grounding"
R_NO_HOME_ROOM = "no_home_room"


def _load_system_prompt(path: Path = PROACTIVE_PROMPT_PATH) -> str:
    """`prompts/proactive.md`: YAML frontmatter delimited by `---`, the
    instruction after the closing `---`. Never edited (contract, kept) —
    this only reads it."""
    text = path.read_text()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text.strip()
    return parts[2].strip()


@dataclass(frozen=True)
class OpenNote:
    """What `decide` reads for one open note — `NoteEntry`'s fields (what
    the model is shown) plus `strikes`/`revisit_at` (what THIS module uses
    to decide whether the model gets to see it at all — neither field is
    rendered, so this is the only place the no-second-raise rule can live)."""

    id: str
    title: str
    closing_condition: str
    created_at: datetime
    room_label: str
    strikes: int = 0
    revisit_at: datetime | None = None

    def as_entry(self) -> NoteEntry:
        return NoteEntry(
            id=self.id,
            title=self.title,
            closing_condition=self.closing_condition,
            created_at=self.created_at,
            room_label=self.room_label,
        )


@dataclass(frozen=True)
class PendingReminder:
    """One row of `reminders`, the shape `act` needs to fire it."""

    id: str
    workspace: str
    note_id: str | None
    due_at: datetime
    intent: str


@dataclass(frozen=True)
class DecideOutcome:
    ok: bool
    call_id: int | None
    reminders_created: tuple[str, ...] = ()
    struck_notes: tuple[str, ...] = ()
    verdicts: tuple[tuple[str | None, str], ...] = ()  # (note_id, verdict)


@dataclass(frozen=True)
class ActOutcome:
    ok: bool
    verdict: str  # 'spoke' | 'silent' | 'failed'
    call_id: int | None = None
    reason: str = ""
    delivery: act_module.Delivery | None = None


def has_been_answered(spoken_at: datetime, transcript: list[TranscriptRow]) -> bool:
    """A human (non-Ora) row anywhere in the transcript after `spoken_at`
    counts as an answer. This does not judge WHETHER the reply actually
    settles the note — that is the model's job on the next pass, reading
    the room fresh; this only decides whether the room stayed silent, which
    is the one signal the strike rule watches."""
    return any((not row.is_ora) and row.ts > spoken_at for row in transcript)


async def strike_unanswered_notes(
    store: Store,
    *,
    notes: list[OpenNote],
    loop_decisions: list[DecisionEntry],
    transcript: list[TranscriptRow],
    now: datetime,
    revisit_after: timedelta = DEFAULT_REVISIT_AFTER,
) -> tuple[list[OpenNote], tuple[str, ...]]:
    """For every note that has never been struck yet but WAS raised
    (`proactive_act` 'spoke', grounded on it) long enough ago
    (`revisit_after`) with no human reply since: increments `note.strikes`
    and sets `note.revisit_at`, both in the store and in the returned copy
    of `notes` — so a single `decide` pass sees the strike immediately,
    without a second read-back. Never raises (R-5): a store failure here
    just leaves that note un-struck for this pass rather than aborting it."""
    updated: list[OpenNote] = []
    struck: list[str] = []
    for note in notes:
        spoke_ts = [
            d.ts
            for d in loop_decisions
            if d.writer == "proactive_act" and d.verdict == "spoke" and d.note_id == note.id
        ]
        if not spoke_ts:
            updated.append(note)
            continue
        last_spoke = max(spoke_ts)
        if now - last_spoke < revisit_after or has_been_answered(last_spoke, transcript):
            updated.append(note)
            continue
        # Strike once per RAISE, not once per note. An earlier version
        # skipped any note with strikes > 0, so `revisit_at` was set once and
        # never moved again — after it passed, the note stayed eligible on
        # every subsequent tick and Ora could re-raise the same thing
        # forever. That is precisely the nagging this module exists to
        # prevent. `revisit_at - <the backoff we last applied>` is when we
        # last struck; a raise newer than that is a new one.
        already_struck_for_this_raise = (
            note.revisit_at is not None
            and last_spoke <= note.revisit_at - revisit_after * max(note.strikes, 1)
        )
        if already_struck_for_this_raise:
            updated.append(note)
            continue
        # Back off further each time: a note the room keeps ignoring should
        # get quieter, not return on the same fixed cadence.
        revisit_at = now + revisit_after * (note.strikes + 1)
        try:
            await store.execute(
                "UPDATE note SET strikes = strikes + 1, revisit_at = $2 WHERE id = $1",
                note.id,
                revisit_at,
            )
        except Exception:  # noqa: BLE001 — never raises into the loop (R-5)
            log.warning("proactive: failed to strike note %s", note.id, exc_info=True)
            updated.append(note)
            continue
        struck.append(note.id)
        updated.append(replace(note, strikes=note.strikes + 1, revisit_at=revisit_at))
    return updated, tuple(struck)


def eligible_for_raise(note: OpenNote, *, now: datetime) -> bool:
    """No second raise: a never-struck note is always eligible; a struck
    one is eligible again only once `revisit_at` has passed. This is the
    ONE line the strike test proves by reverting."""
    if note.strikes <= 0:
        return True
    return note.revisit_at is not None and now >= note.revisit_at


def _parse_decide_content(content: str) -> list[Any] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    entries = parsed.get("decisions")
    if not isinstance(entries, list):
        return None
    return entries


def _parse_when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # "ISO-8601 with an offset" (prompt's own words) — a naive time is refused
    return dt


async def _next_reminder_id(store: Store, workspace: str) -> str:
    count = await store.fetchval(
        "SELECT COUNT(*) FROM reminders WHERE workspace = $1", workspace
    )
    return f"R{(count or 0) + 1}"


async def decide(
    model: Any,  # a loop.model.ModelClient (or anything with .complete_json)
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    notes: list[OpenNote],
    standing: StandingEntry | None,
    loop_decisions: list[DecisionEntry],
    transcript: list[TranscriptRow],
    now: datetime,
    reasoning_effort: str = "low",
    revisit_after: timedelta = DEFAULT_REVISIT_AFTER,
) -> DecideOutcome:
    """One pass: strike whatever went unanswered, show the model only what
    it is allowed to raise again, and turn its answer into `reminders` +
    `decisions` rows. Never raises into the loop; every failure mode
    degrades to a `decline` (never a `remind`)."""
    notes, struck = await strike_unanswered_notes(
        store,
        notes=notes,
        loop_decisions=loop_decisions,
        transcript=transcript,
        now=now,
        revisit_after=revisit_after,
    )
    eligible = {n.id: n for n in notes if eligible_for_raise(n, now=now)}

    system = _load_system_prompt()
    prompt = "\n\n".join(
        block
        for block in (
            render_notes([n.as_entry() for n in eligible.values()], now=now),
            render_standing(standing, now=now),
            render_loop_decisions(loop_decisions, now=now, writers=()),
            render_transcript(transcript, now=now),
        )
        if block
    ) or "(nothing open, nothing pending, nothing said)"

    completion = await model.complete_json(
        stage=DECIDE_STAGE,
        system=system,
        prompt=prompt,
        reasoning_effort=reasoning_effort,
        platform=platform,
        conversation_id=conversation_id,
    )

    if not completion.ok:
        reason = R_MODEL_TIMEOUT if completion.error_kind == "timeout" else R_MODEL_ERROR
        await _record_decline(
            store,
            workspace=workspace,
            platform=platform,
            conversation_id=conversation_id,
            note_id=None,
            reason=reason,
            call_id=completion.call_id,
        )
        return DecideOutcome(ok=False, call_id=completion.call_id, struck_notes=struck)

    entries = _parse_decide_content(completion.content)
    if entries is None:
        await _record_decline(
            store,
            workspace=workspace,
            platform=platform,
            conversation_id=conversation_id,
            note_id=None,
            reason=R_UNPARSEABLE,
            call_id=completion.call_id,
        )
        return DecideOutcome(ok=False, call_id=completion.call_id, struck_notes=struck)

    created: list[str] = []
    verdicts: list[tuple[str | None, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        note_id = entry.get("note")
        note_id = note_id if isinstance(note_id, str) and note_id else None
        action = entry.get("action")

        if note_id is not None and note_id not in eligible:
            # a note the model was never shown, or hallucinated — never grounded
            await _record_decline(
                store,
                workspace=workspace,
                platform=platform,
                conversation_id=conversation_id,
                note_id=note_id,
                reason=R_UNKNOWN_NOTE,
                call_id=completion.call_id,
            )
            verdicts.append((note_id, "decline"))
            continue

        if action == "remind":
            due = _parse_when(entry.get("when"))
            text = entry.get("text")
            text = text.strip() if isinstance(text, str) else ""
            if due is None or not text or note_id is None:
                await _record_decline(
                    store,
                    workspace=workspace,
                    platform=platform,
                    conversation_id=conversation_id,
                    note_id=note_id,
                    reason=R_REMIND_MISSING_FIELDS,
                    call_id=completion.call_id,
                )
                verdicts.append((note_id, "decline"))
                continue
            reminder_id = await _create_reminder(
                store,
                workspace=workspace,
                note_id=note_id,
                due_at=due,
                intent=text,
                now=now,
                call_id=completion.call_id,
            )
            if reminder_id is None:
                await decisions.record(
                    store,
                    workspace=workspace,
                    writer=DECIDE_STAGE,
                    verdict="failed",
                    platform=platform,
                    conversation_id=conversation_id,
                    note_id=note_id,
                    grounded_on=f"note:{note_id}",
                    reason=R_DB_ERROR,
                    call_id=completion.call_id,
                    ts=now,
                )
                verdicts.append((note_id, "failed"))
                continue
            await decisions.record(
                store,
                workspace=workspace,
                writer=DECIDE_STAGE,
                verdict="remind",
                platform=platform,
                conversation_id=conversation_id,
                note_id=note_id,
                reminder_id=reminder_id,
                grounded_on=f"note:{note_id}",
                reason=R_SCHEDULED,
                call_id=completion.call_id,
                ts=now,
            )
            created.append(reminder_id)
            verdicts.append((note_id, "remind"))
        elif action == "decline":
            await _record_decline(
                store,
                workspace=workspace,
                platform=platform,
                conversation_id=conversation_id,
                note_id=note_id,
                reason=R_DECLINED,
                call_id=completion.call_id,
            )
            verdicts.append((note_id, "decline"))
        else:
            # an unrecognised action is an unknown verdict — fail closed (rule 1)
            await _record_decline(
                store,
                workspace=workspace,
                platform=platform,
                conversation_id=conversation_id,
                note_id=note_id,
                reason=R_UNKNOWN_ACTION,
                call_id=completion.call_id,
            )
            verdicts.append((note_id, "decline"))

    return DecideOutcome(
        ok=True,
        call_id=completion.call_id,
        reminders_created=tuple(created),
        struck_notes=struck,
        verdicts=tuple(verdicts),
    )


async def _record_decline(
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    note_id: str | None,
    reason: str,
    call_id: int | None,
) -> None:
    try:
        await decisions.record(
            store,
            workspace=workspace,
            writer=DECIDE_STAGE,
            verdict="decline",
            platform=platform,
            conversation_id=conversation_id,
            note_id=note_id,
            grounded_on=f"note:{note_id}" if note_id else None,
            reason=reason,
            call_id=call_id,
        )
    except Exception:  # noqa: BLE001 — never raises into the loop (R-5)
        log.warning("proactive: failed to record a decline", exc_info=True)


async def _create_reminder(
    store: Store,
    *,
    workspace: str,
    note_id: str,
    due_at: datetime,
    intent: str,
    now: datetime,
    call_id: int | None,
) -> str | None:
    try:
        reminder_id = await _next_reminder_id(store, workspace)
        await store.execute(
            """INSERT INTO reminders
               (id, workspace, note_id, due_at, state, intent, intent_len,
                created_at, created_call_id)
               VALUES ($1,$2,$3,$4,'pending',$5,$6,$7,$8)""",
            reminder_id,
            workspace,
            note_id,
            due_at,
            intent,
            len(intent),
            now,
            call_id,
        )
        return reminder_id
    except Exception:  # noqa: BLE001 — never raises into the loop (R-5)
        log.warning("proactive: failed to create a reminder for note %s", note_id, exc_info=True)
        return None


_ACT_SYSTEM = (
    "You are Ora. Earlier you set a reminder for yourself, alone, about one "
    "open note — nobody asked you to. That moment has come. Below is the "
    "note, the room's standing, what you and the room have said, and your "
    "own record of past raises. Decide once more, fresh, whether to speak: "
    "the room may have already settled this since you set the reminder, and "
    "silence stays the right, ordinary answer. Reply with JSON only: "
    '{"speak": true|false, "text": "...", "why": "..."}. `text` is the '
    "message to send to the room verbatim (required when speak is true, "
    "otherwise omit or leave empty) — write it in your own voice, short, "
    "as one message. `why` is one short sentence for your own record. "
    "Never restate the note as a question if nothing has changed; never "
    "invent detail no block below supports."
)


def home_room(config: Config) -> Room | None:
    """The workspace's home room, resolved via `rooms.toml`'s
    `home_room` label. `None` when unconfigured — `act` fails closed to
    `silent` rather than guessing a room to speak in."""
    workspace = config.workspaces.get(config.workspace)
    if workspace is None or not workspace.home_room:
        return None
    raw = workspace.home_room

    # `rooms.toml` writes this as `platform:conversation_id`, e.g.
    # `signal:bMo1TAT…=`. Matching it against `room.label` ("Signal")
    # silently resolved to None on the real config, which fails closed —
    # so the proactive path simply never fired and said nothing about it.
    # A Signal group id is base64 (`+`, `/`, `=`) and never contains a
    # colon, so one split is unambiguous.
    if ":" in raw:
        platform, _, conversation_id = raw.partition(":")
        room = config.room_for(platform.strip(), conversation_id.strip())
        if room is not None:
            return room

    for room in config.rooms.values():
        if room.label == raw:
            return room
    return None


async def act(
    model: Any,  # a loop.model.ModelClient (or anything with .complete_json)
    store: Store,
    config: Config,
    people: PeopleDirectory,
    *,
    reminder: PendingReminder,
    notes: list[OpenNote],
    standing: StandingEntry | None,
    loop_decisions: list[DecisionEntry],
    transcript: list[TranscriptRow],
    now: datetime,
    reasoning_effort: str = "low",
    session: Any = None,
    timeout_seconds: float = 15.0,
) -> ActOutcome:
    """A reminder has come due. Resolve it (never leave a fired reminder
    `pending`) and record exactly one `proactive_act` row. Fail closed at
    every step — an ungrounded reminder (no `note_id`), a bad model
    response, or a delivery failure all end in silence or `failed`, never a
    speak that skipped grounding."""
    room = home_room(config)
    if room is None:
        await _resolve_reminder(store, reminder_id=reminder.id, now=now, state="failed")
        await _record_act(
            store,
            workspace=reminder.workspace,
            platform="",
            conversation_id="",
            note_id=reminder.note_id,
            reminder_id=reminder.id,
            verdict="silent",
            grounded_on=None,
            reason=R_NO_HOME_ROOM,
            call_id=None,
            now=now,
        )
        return ActOutcome(ok=False, verdict="silent", reason=R_NO_HOME_ROOM)

    if not reminder.note_id:
        # nothing to ground a speak on — a speak without grounded_on is a hold (fail closed)
        await _resolve_reminder(store, reminder_id=reminder.id, now=now, state="done")
        await _record_act(
            store,
            workspace=reminder.workspace,
            platform=room.platform,
            conversation_id=room.conversation_id,
            note_id=None,
            reminder_id=reminder.id,
            verdict="silent",
            grounded_on=None,
            reason=R_NO_GROUNDING,
            call_id=None,
            now=now,
        )
        return ActOutcome(ok=True, verdict="silent", reason=R_NO_GROUNDING)

    prompt_parts = [
        f"<reminder note=\"{reminder.note_id}\">{reminder.intent}</reminder>",
        render_notes([n.as_entry() for n in notes], now=now),
        render_standing(standing, now=now),
        render_loop_decisions(loop_decisions, now=now, writers=()),
        render_transcript(transcript, now=now),
    ]
    prompt = "\n\n".join(p for p in prompt_parts if p)

    completion = await model.complete_json(
        stage=ACT_STAGE,
        system=_ACT_SYSTEM,
        prompt=prompt,
        reasoning_effort=reasoning_effort,
        platform=room.platform,
        conversation_id=room.conversation_id,
    )

    if not completion.ok:
        reason = R_MODEL_TIMEOUT if completion.error_kind == "timeout" else R_MODEL_ERROR
        return await _hold(store, reminder, room, reason, completion.call_id, now)

    parsed = _parse_act_content(completion.content)
    if parsed is None:
        return await _hold(store, reminder, room, R_UNPARSEABLE, completion.call_id, now)

    speak, text = parsed
    if not speak:
        return await _hold(store, reminder, room, R_HELD, completion.call_id, now)
    if not text:
        return await _hold(store, reminder, room, R_EMPTY_TEXT, completion.call_id, now)

    delivery = await act_module.deliver(
        config,
        store,
        room.platform,
        room.conversation_id,
        text,
        people,
        session=session,
        timeout_seconds=timeout_seconds,
    )

    if delivery.ok:
        await _resolve_reminder(store, reminder_id=reminder.id, now=now, state="done")
        await _record_act(
            store,
            workspace=reminder.workspace,
            platform=room.platform,
            conversation_id=room.conversation_id,
            note_id=reminder.note_id,
            reminder_id=reminder.id,
            verdict="spoke",
            grounded_on=f"note:{reminder.note_id}",
            reason=R_SPOKEN,
            call_id=completion.call_id,
            now=now,
        )
        return ActOutcome(ok=True, verdict="spoke", call_id=completion.call_id, delivery=delivery)

    await _resolve_reminder(store, reminder_id=reminder.id, now=now, state="failed")
    reason = f"delivery_failed:{delivery.reason}" if delivery.reason else "delivery_failed"
    await _record_act(
        store,
        workspace=reminder.workspace,
        platform=room.platform,
        conversation_id=room.conversation_id,
        note_id=reminder.note_id,
        reminder_id=reminder.id,
        verdict="failed",
        grounded_on=f"note:{reminder.note_id}",
        reason=reason,
        call_id=completion.call_id,
        now=now,
    )
    return ActOutcome(ok=False, verdict="failed", call_id=completion.call_id, delivery=delivery)


async def _hold(
    store: Store,
    reminder: PendingReminder,
    room: Room,
    reason: str,
    call_id: int | None,
    now: datetime,
) -> ActOutcome:
    """Fail-closed / deliberate-silence path shared by every "don't speak"
    branch of `act`: resolve the reminder as fired-but-quiet and record
    `silent`. Never sends anything."""
    await _resolve_reminder(store, reminder_id=reminder.id, now=now, state="done")
    await _record_act(
        store,
        workspace=reminder.workspace,
        platform=room.platform,
        conversation_id=room.conversation_id,
        note_id=reminder.note_id,
        reminder_id=reminder.id,
        verdict="silent",
        grounded_on=f"note:{reminder.note_id}" if reminder.note_id else None,
        reason=reason,
        call_id=call_id,
        now=now,
    )
    return ActOutcome(ok=True, verdict="silent", call_id=call_id, reason=reason)


def _parse_act_content(content: str) -> tuple[bool, str] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    speak = parsed.get("speak")
    if not isinstance(speak, bool):
        return None
    text = parsed.get("text")
    text = text.strip() if isinstance(text, str) else ""
    return speak, text


async def _resolve_reminder(
    store: Store, *, reminder_id: str, now: datetime, state: str
) -> None:
    try:
        await store.execute(
            "UPDATE reminders SET state = $2, resolved_at = $3, resolved_by = $4 WHERE id = $1",
            reminder_id,
            state,
            now,
            "fired",
        )
    except Exception:  # noqa: BLE001 — never raises into the loop (R-5)
        log.warning("proactive: failed to resolve reminder %s", reminder_id, exc_info=True)


async def _record_act(
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    note_id: str | None,
    reminder_id: str,
    verdict: str,
    grounded_on: str | None,
    reason: str,
    call_id: int | None,
    now: datetime,
) -> None:
    try:
        await decisions.record(
            store,
            workspace=workspace,
            writer=ACT_STAGE,
            verdict=verdict,
            platform=platform,
            conversation_id=conversation_id,
            note_id=note_id,
            reminder_id=reminder_id,
            grounded_on=grounded_on,
            reason=reason,
            call_id=call_id,
            ts=now,
        )
    except Exception:  # noqa: BLE001 — never raises into the loop (R-5)
        log.warning("proactive: failed to record an act decision", exc_info=True)


__all__ = [
    "ACT_STAGE",
    "DECIDE_STAGE",
    "ActOutcome",
    "DecideOutcome",
    "OpenNote",
    "PendingReminder",
    "act",
    "decide",
    "eligible_for_raise",
    "has_been_answered",
    "home_room",
    "strike_unanswered_notes",
]
