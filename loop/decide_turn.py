"""The participation turn — the expensive judgement behind the relevance
gate (`design/03-components-and-provenance.md` line 71, `design/
01-architecture.md` line 63). Reads `prompts/turn.md`'s instruction (never
edited — CLAUDE.md), the room via `render.py`'s blocks, and reduces one
`complete_json` call to exactly one of `speak` / `hold` / `cancel`.

**The one brake that is not a preference** (ORA-10): a `speak` whose
`grounded_on` does not survive `_grounded_on_is_valid` is downgraded to a
`hold` before `loop/act.deliver` is ever called — the model's own wish to
talk is not the thing that decides whether it talks. Proved by
`tests/test_decide_turn.py::test_ungrounded_speak_is_hold_and_never_delivers`,
which reddens if the check above `deliver(...)` is removed (`design/
01-architecture.md`'s own name for this property: `test_ungrounded_speak_is_hold`).

**Fail closed everywhere** (R-5): a timeout, any other exception out of
`model.complete_json`, a non-`stop` finish, or an unparseable/unrecognised
JSON body all reduce to the SAME safe outcome — `decisions` verdict
`'silent'`, never `'spoke'`. `decide()` itself never raises into the loop
(the same discipline `loop/search.py`, `loop/route.py`, `loop/memory.py`,
`loop/vision.py` hold): a broad catch at the top is a second, outer net
around the narrower one directly around the model call.

Every model-written row points at its `model_calls` row: `completion.
call_id` travels into `decisions.record` on every path, including a hold.

A note's TITLE never reaches this module in the first place —
`render_loop_decisions`/`render_notes` already enforce that (rule 5); this
module only ever extracts an id out of `grounded_on` or `reminder_id`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from loop import decisions, tools
from loop.act import deliver
from loop.config import Config
from loop.model import ModelClient
from loop.people import PeopleDirectory
from loop.render import (
    DecisionEntry,
    NoteEntry,
    StandingEntry,
    TranscriptRow,
    render_loop_decisions,
    render_notes,
    render_peer_card,
    render_self_card,
    render_standing,
    render_transcript,
)
from loop.store import Store

log = logging.getLogger("ora.participation")

STAGE = "turn"
WRITER = "participation"
REASONING_EFFORT = "low"  # ORA-14: the expensive turn, pinned here — never a caller default
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "turn.md"

# reason codes for `decisions.reason` — short labels, never model prose (DEC-179)
REASON_BAD_JSON = "held_bad_json"
REASON_EMPTY_TEXT = "held_empty_text"
REASON_UNGROUNDED = "held_ungrounded"  # the brake
REASON_HOLD = "held"  # the model's own plain hold — healthy, not a failure
REASON_BAD_CANCEL = "held_bad_cancel"
REASON_CANCEL_NOT_FOUND = "held_cancel_not_found"
REASON_UNKNOWN_VERDICT = "held_unknown_verdict"
REASON_DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True)
class TurnResult:
    """What the turn decided, already reduced to the `decisions` table's
    own vocabulary (`'spoke' | 'silent' | 'cancelled' | 'failed'`) —
    never the model's raw verdict word, so a caller never has to
    re-translate one vocabulary into another."""

    verdict: str
    reason: str = ""
    grounded_on: str | None = None
    note_id: str | None = None
    reminder_id: str | None = None
    text: str = ""
    call_id: int | None = None
    decision_id: int | None = None
    delivery_ok: bool | None = None


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """`prompts/turn.md`'s instruction only — everything after the closing
    `---` of its YAML frontmatter. Read once and cached: the file is never
    edited at runtime, and CMS says interpret once (`~/.claude/CMS.md`)."""
    lines = PROMPT_PATH.read_text().splitlines()
    delimiters = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(delimiters) < 2:
        return "\n".join(lines).strip()
    return "\n".join(lines[delimiters[1] + 1 :]).strip()


def _build_prompt(
    *,
    now: datetime,
    transcript: list[TranscriptRow],
    standing: StandingEntry | None,
    notes: list[NoteEntry],
    loop_decisions: list[DecisionEntry],
    loop_decision_writers: tuple[str, ...],
    self_card_conclusions: list[str],
    peer_cards: list[tuple[str, list[str]]],
    tool_run: Any = None,
) -> str:
    """Every block `render.py` owns, joined — never hand-rolled prompt
    text. `loop_decisions` defaults to every writer (not just this one's
    own past turns): a pending reminder worth cancelling was very likely
    raised by `proactive_decide`, not by this turn."""
    blocks = [
        render_transcript(transcript, now=now),
        render_standing(standing, now=now),
        render_notes(notes, now=now),
        render_loop_decisions(loop_decisions, now=now, writers=loop_decision_writers),
        render_self_card(self_card_conclusions),
    ]
    blocks.extend(render_peer_card(person, facts) for person, facts in peer_cards)
    if tool_run is not None:
        blocks.extend(tools.blocks(tool_run))
    return "\n\n".join(block for block in blocks if block)


def _parse_payload(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _grounded_on_is_valid(
    value: Any,
    *,
    valid_note_ids: frozenset[str] | None = None,
    standing_present: bool = True,
    tool_run: Any = None,
) -> bool:
    """`note:<id>` | `standing` | `tool:<name>` (prompts/turn.md's own
    contract) — anything else (missing, wrong type, empty id/name, a
    made-up shape) fails, which is what turns the `speak` into a `hold`
    before `deliver` is ever reached.

    Checked against REALITY, not just shape (audit finding): `note:<id>`
    must name a note this turn was actually shown, and `standing` requires
    a standing paragraph to exist. A shape-only check passes
    `note:deadbeef` — a citation to nothing — and `deliver` then sends a
    message whose provenance trail cannot be followed back to anything.
    `tag.py` already validates this way; the participation turn is the path
    most likely to speak on stage, so the hole mattered more here.

    `tool:` is credited only when that tool actually ran and returned
    something this turn (`tool_run.grounded`). The ambient turn now has the
    same tools as the tag path, so the question is no longer "could a tool
    have run here" but "did this one, and did it come back with anything" —
    a claimed `tool:route` whose lookup returned `no_route` grounds nothing.
    """
    if not isinstance(value, str):
        return False
    if value == "standing":
        return standing_present
    if value.startswith("note:"):
        note_id = value[len("note:") :]
        if not note_id:
            return False
        return valid_note_ids is None or note_id in valid_note_ids
    if value.startswith("tool:"):
        return tool_run is not None and value in tool_run.grounded
    return False


async def _cancel_reminder(
    store: Store, *, workspace: str, reminder_id: str, now: datetime
) -> bool:
    """Flips a PENDING reminder to `cancelled`; anything else (already
    resolved, a different workspace, an id nobody raised) is "not
    honoured" in `prompts/turn.md`'s own words — zero rows affected comes
    back as `False`, never asserted as a cancel that did not happen."""
    row = await store.fetchrow(
        """UPDATE reminders
           SET state = 'cancelled', resolved_at = $1, resolved_by = 'participation:cancel'
           WHERE id = $2 AND workspace = $3 AND state = 'pending'
           RETURNING id""",
        now,
        reminder_id,
        workspace,
    )
    return row is not None


async def _record(
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    verdict: str,
    ts: datetime,
    grounded_on: str | None = None,
    note_id: str | None = None,
    reminder_id: str | None = None,
    reason: str = "",
    call_id: int | None = None,
) -> int:
    return await decisions.record(
        store,
        workspace=workspace,
        writer=WRITER,
        verdict=verdict,
        platform=platform,
        conversation_id=conversation_id,
        note_id=note_id,
        reminder_id=reminder_id,
        grounded_on=grounded_on,
        reason=reason,
        call_id=call_id,
        ts=ts,
    )


async def _hold(
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    now: datetime,
    reason: str,
    call_id: int | None = None,
    reminder_id: str | None = None,
) -> TurnResult:
    decision_id = await _record(
        store,
        workspace=workspace,
        platform=platform,
        conversation_id=conversation_id,
        verdict="silent",
        reason=reason,
        call_id=call_id,
        ts=now,
        reminder_id=reminder_id,
    )
    return TurnResult(
        verdict="silent",
        reason=reason,
        call_id=call_id,
        decision_id=decision_id,
        reminder_id=reminder_id,
    )


async def decide(
    *,
    model: ModelClient,
    config: Config,
    store: Store,
    people: PeopleDirectory,
    workspace: str,
    platform: str,
    conversation_id: str,
    now: datetime,
    transcript: list[TranscriptRow],
    standing: StandingEntry | None = None,
    notes: list[NoteEntry] | None = None,
    loop_decisions: list[DecisionEntry] | None = None,
    loop_decision_writers: tuple[str, ...] = (),
    self_card_conclusions: list[str] | None = None,
    peer_cards: list[tuple[str, list[str]]] | None = None,
    tool_run: Any = None,
    session: Any = None,
    timeout_seconds: float | None = None,
) -> TurnResult:
    """Run one participation turn and land its row. `now` is comprehended
    once by the caller and threaded through every render call and every
    write here (transcript ages, `decisions.ts`, a cancel's
    `resolved_at`) — never re-read mid-turn.

    Never raises (R-5): the outer `try` is a second net around the one
    directly on the model call, so a persistence or delivery failure
    degrades the same way a bad model response does — `silent`, not an
    exception into the loop.
    """
    try:
        return await _decide(
            model=model,
            config=config,
            store=store,
            people=people,
            workspace=workspace,
            platform=platform,
            conversation_id=conversation_id,
            now=now,
            transcript=transcript,
            standing=standing,
            notes=notes or [],
            loop_decisions=loop_decisions or [],
            loop_decision_writers=loop_decision_writers,
            self_card_conclusions=self_card_conclusions or [],
            peer_cards=peer_cards or [],
            tool_run=tool_run,
            session=session,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (R-5)
        log.exception("participation turn failed outside the model call")
        return TurnResult(verdict="silent", reason=f"held_error_{type(exc).__name__}")


async def _decide(
    *,
    model: ModelClient,
    config: Config,
    store: Store,
    people: PeopleDirectory,
    workspace: str,
    platform: str,
    conversation_id: str,
    now: datetime,
    transcript: list[TranscriptRow],
    standing: StandingEntry | None,
    notes: list[NoteEntry],
    loop_decisions: list[DecisionEntry],
    loop_decision_writers: tuple[str, ...],
    self_card_conclusions: list[str],
    peer_cards: list[tuple[str, list[str]]],
    tool_run: Any,
    session: Any,
    timeout_seconds: float | None,
) -> TurnResult:
    system = _system_prompt()
    prompt = _build_prompt(
        now=now,
        transcript=transcript,
        standing=standing,
        notes=notes,
        loop_decisions=loop_decisions,
        loop_decision_writers=loop_decision_writers,
        self_card_conclusions=self_card_conclusions,
        peer_cards=peer_cards,
        tool_run=tool_run,
    )

    try:
        completion = await model.complete_json(
            stage=STAGE,
            system=system,
            prompt=prompt,
            reasoning_effort=REASONING_EFFORT,
            platform=platform,
            conversation_id=conversation_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — a raw client's transport errors, fail closed
        reason = f"held_error_{type(exc).__name__}"
        return await _hold(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            now=now, reason=reason,
        )

    if not completion.ok:
        # timeout, a non-`stop` finish (e.g. "length") — model.py's own
        # error_kind is the real, grounded signal; never invented here.
        reason = f"held_{completion.error_kind or 'error'}"
        return await _hold(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            now=now, reason=reason, call_id=completion.call_id,
        )

    payload = _parse_payload(completion.content)
    if payload is None:
        return await _hold(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            now=now, reason=REASON_BAD_JSON, call_id=completion.call_id,
        )

    verdict_word = payload.get("verdict")

    if verdict_word == "hold":
        return await _hold(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            now=now, reason=REASON_HOLD, call_id=completion.call_id,
        )

    if verdict_word == "speak":
        text = payload.get("text")
        grounded_on = payload.get("grounded_on")
        if not isinstance(text, str) or not text.strip():
            return await _hold(
                store, workspace=workspace, platform=platform, conversation_id=conversation_id,
                now=now, reason=REASON_EMPTY_TEXT, call_id=completion.call_id,
            )
        offered_note_ids = frozenset(n.id for n in (notes or []))
        if not _grounded_on_is_valid(
            grounded_on,
            valid_note_ids=offered_note_ids,
            standing_present=standing is not None and bool(standing.body.strip()),
            tool_run=tool_run,
        ):
            # THE BRAKE (ORA-10): a speak with nothing behind it never
            # reaches `deliver` — see test_ungrounded_speak_is_hold_and_never_delivers.
            return await _hold(
                store, workspace=workspace, platform=platform, conversation_id=conversation_id,
                now=now, reason=REASON_UNGROUNDED, call_id=completion.call_id,
            )
        note_id = grounded_on.split(":", 1)[1] if grounded_on.startswith("note:") else None

        delivery = await deliver(
            config, store, platform, conversation_id, text, people,
            session=session, timeout_seconds=timeout_seconds or 15.0,
        )
        verdict = "spoke" if delivery.ok else "failed"
        reason = "" if delivery.ok else REASON_DELIVERY_FAILED
        decision_id = await _record(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            verdict=verdict, grounded_on=grounded_on, note_id=note_id, reason=reason,
            call_id=completion.call_id, ts=now,
        )
        return TurnResult(
            verdict=verdict, reason=reason, grounded_on=grounded_on, note_id=note_id,
            text=text, call_id=completion.call_id, decision_id=decision_id,
            delivery_ok=delivery.ok,
        )

    if verdict_word == "cancel":
        reminder_id = payload.get("reminder_id")
        if not isinstance(reminder_id, str) or not reminder_id.strip():
            return await _hold(
                store, workspace=workspace, platform=platform, conversation_id=conversation_id,
                now=now, reason=REASON_BAD_CANCEL, call_id=completion.call_id,
            )
        cancelled = await _cancel_reminder(
            store, workspace=workspace, reminder_id=reminder_id, now=now
        )
        if not cancelled:
            return await _hold(
                store, workspace=workspace, platform=platform, conversation_id=conversation_id,
                now=now, reason=REASON_CANCEL_NOT_FOUND, call_id=completion.call_id,
                reminder_id=reminder_id,
            )
        decision_id = await _record(
            store, workspace=workspace, platform=platform, conversation_id=conversation_id,
            verdict="cancelled", reminder_id=reminder_id, call_id=completion.call_id, ts=now,
        )
        return TurnResult(
            verdict="cancelled", reminder_id=reminder_id, call_id=completion.call_id,
            decision_id=decision_id,
        )

    # a verdict the prompt did not define — fail closed, never guess
    return await _hold(
        store, workspace=workspace, platform=platform, conversation_id=conversation_id,
        now=now, reason=REASON_UNKNOWN_VERDICT, call_id=completion.call_id,
    )
