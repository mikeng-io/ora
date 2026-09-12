"""Fake-model, fake-store coverage for `loop/decide_proactive.py` — no
network, no real Postgres. `FakeModelClient` is `tests/test_journey.py`'s
pattern (a per-stage response queue that raises `AssertionError` on an
unscripted stage — used here to also PROVE a guard fired: if a test's fake
client has nothing scripted and no error is raised, the model was never
called). `FakeStore` is `tests/test_vision.py`'s recording-fake pattern,
extended to dispatch on the query text since this module writes to three
tables (`decisions`, `reminders`, `note`) through one `Store` interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loop.config import Clocks, Config, Env, Room, Workspace
from loop.decide_proactive import (
    R_MODEL_TIMEOUT,
    R_NO_GROUNDING,
    R_NO_HOME_ROOM,
    R_REMIND_MISSING_FIELDS,
    R_UNKNOWN_NOTE,
    R_UNPARSEABLE,
    OpenNote,
    PendingReminder,
    act,
    decide,
    eligible_for_raise,
    has_been_answered,
    home_room,
    strike_unanswered_notes,
)
from loop.people import PeopleDirectory
from loop.render import DecisionEntry, TranscriptRow

NOW = datetime(2026, 9, 12, 8, 0, 0, tzinfo=UTC)
WORKSPACE = "demo"
PLATFORM = "whatsapp"
CONVERSATION_ID = "w1"


# --- fakes -------------------------------------------------------------


@dataclass
class FakeCompletion:
    content: str
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 7
    error_kind: str | None = None


@dataclass
class FakeModelClient:
    responses: dict[str, list[FakeCompletion]] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    def script(self, stage: str, completion: FakeCompletion) -> None:
        self.responses.setdefault(stage, []).append(completion)

    async def complete_json(self, *, stage: str, system: str, prompt: str, **kwargs: object):
        self.calls.append({"stage": stage, "system": system, "prompt": prompt, **kwargs})
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r} — script it first")
        return queue.pop(0)


@dataclass
class FakeStore:
    reminders: dict[str, dict] = field(default_factory=dict)
    note_strikes: dict[str, dict] = field(default_factory=dict)
    decision_rows: list[dict] = field(default_factory=list)
    messages: list[tuple] = field(default_factory=list)
    raise_on_reminder_insert: bool = False

    async def fetchval(self, query: str, *args: object):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO decisions"):
            row = {
                "workspace": args[0],
                "writer": args[1],
                "verdict": args[2],
                "platform": args[3],
                "conversation_id": args[4],
                "note_id": args[5],
                "reminder_id": args[6],
                "grounded_on": args[7],
                "reason": args[8],
                "call_id": args[9],
                "ts": args[10],
            }
            self.decision_rows.append(row)
            return len(self.decision_rows)
        if q.startswith("SELECT COUNT(*) FROM reminders"):
            workspace = args[0]
            return sum(1 for r in self.reminders.values() if r["workspace"] == workspace)
        if q.startswith("INSERT INTO messages"):
            self.messages.append(args)
            return len(self.messages)
        raise AssertionError(f"unexpected fetchval: {query}")

    async def execute(self, query: str, *args: object):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO reminders"):
            if self.raise_on_reminder_insert:
                raise RuntimeError("boom")
            rid, workspace, note_id, due_at, intent, intent_len, created_at, call_id = args
            self.reminders[rid] = {
                "id": rid,
                "workspace": workspace,
                "note_id": note_id,
                "due_at": due_at,
                "state": "pending",
                "intent": intent,
                "intent_len": intent_len,
                "created_at": created_at,
                "created_call_id": call_id,
                "resolved_at": None,
                "resolved_by": None,
            }
            return "INSERT 1"
        if q.startswith("UPDATE reminders SET state"):
            rid, state, resolved_at, resolved_by = args
            self.reminders[rid].update(
                state=state, resolved_at=resolved_at, resolved_by=resolved_by
            )
            return "UPDATE 1"
        if q.startswith("UPDATE note SET strikes"):
            note_id, revisit_at = args
            row = self.note_strikes.setdefault(note_id, {"strikes": 0, "revisit_at": None})
            row["strikes"] += 1
            row["revisit_at"] = revisit_at
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {query}")


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple] = []

    def post(self, url: str, json: object = None, timeout: object = None) -> _FakeResponse:
        self.calls.append((url, json))
        return _FakeResponse(self.status)


def _note(id_: str = "N1", strikes: int = 0, revisit_at: datetime | None = None) -> OpenNote:
    return OpenNote(
        id=id_,
        title="dinner plans",
        closing_condition="a time is agreed",
        created_at=NOW - timedelta(hours=2),
        room_label="Signal",
        strikes=strikes,
        revisit_at=revisit_at,
    )


def _config(home_label: str = "Signal") -> Config:
    return Config(
        env=Env(signal_base_url="http://signal.local"),
        clocks=Clocks(),
        workspaces={WORKSPACE: Workspace(name=WORKSPACE, home_room=home_label)},
        rooms={("signal", "g1"): Room("signal", "g1", home_label)},
    )


def _seed_reminder(store: FakeStore, id_: str = "R1", note_id: str | None = "N1") -> None:
    """A reminder `act` fires must already exist as a `pending` row —
    `act` never creates one, only `decide` does."""
    store.reminders[id_] = {
        "id": id_,
        "workspace": WORKSPACE,
        "note_id": note_id,
        "state": "pending",
        "resolved_at": None,
        "resolved_by": None,
    }


# --- decide: remind ------------------------------------------------------


async def test_remind_creates_pending_reminder_and_records_remind() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N1",
                            "action": "remind",
                            "when": "2026-09-12T16:00:00+08:00",
                            "text": "ask where dinner is",
                            "why": "nobody has said where yet",
                        }
                    ]
                }
            ),
            call_id=42,
        ),
    )
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.ok
    assert outcome.reminders_created == ("R1",)
    reminder = store.reminders["R1"]
    assert reminder["note_id"] == "N1"
    assert reminder["state"] == "pending"
    assert reminder["created_call_id"] == 42

    remind_rows = [r for r in store.decision_rows if r["verdict"] == "remind"]
    assert len(remind_rows) == 1
    assert remind_rows[0]["note_id"] == "N1"
    assert remind_rows[0]["reminder_id"] == "R1"
    assert remind_rows[0]["call_id"] == 42
    assert remind_rows[0]["reason"] == "scheduled"  # a code label, never model prose
    assert "nobody has said" not in remind_rows[0]["reason"]


async def test_decline_records_decline_and_creates_nothing() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {"decisions": [{"note": "N1", "action": "decline", "why": "settled"}]}
            )
        ),
    )
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.ok
    assert outcome.reminders_created == ()
    assert store.reminders == {}
    assert len(store.decision_rows) == 1
    assert store.decision_rows[0]["verdict"] == "decline"


async def test_remind_missing_when_field_declines_that_entry() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {"decisions": [{"note": "N1", "action": "remind", "text": "ask"}]}
            )
        ),
    )
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.ok
    assert outcome.reminders_created == ()
    assert store.decision_rows[0]["verdict"] == "decline"
    assert store.decision_rows[0]["reason"] == R_REMIND_MISSING_FIELDS


async def test_remind_with_a_naive_when_is_refused() -> None:
    """The prompt requires "ISO-8601 with an offset" — a naive timestamp
    does not qualify, even though `datetime.fromisoformat` would parse it."""
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N1",
                            "action": "remind",
                            "when": "2026-09-12T16:00:00",
                            "text": "ask",
                        }
                    ]
                }
            )
        ),
    )
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.reminders_created == ()
    assert store.decision_rows[0]["reason"] == R_REMIND_MISSING_FIELDS


# --- no second raise ------------------------------------------------------


async def test_unanswered_raise_increments_strikes() -> None:
    loop_decisions = [
        DecisionEntry(
            writer="proactive_act",
            verdict="spoke",
            room_label="Signal",
            ts=NOW - timedelta(hours=7),
            note_id="N1",
        )
    ]
    store = FakeStore()

    updated, struck = await strike_unanswered_notes(
        store,
        notes=[_note()],
        loop_decisions=loop_decisions,
        transcript=[],  # nobody replied
        now=NOW,
    )

    assert struck == ("N1",)
    assert updated[0].strikes == 1
    assert updated[0].revisit_at is not None
    assert store.note_strikes["N1"]["strikes"] == 1


async def test_a_reply_after_the_raise_prevents_a_strike() -> None:
    loop_decisions = [
        DecisionEntry(
            writer="proactive_act",
            verdict="spoke",
            room_label="Signal",
            ts=NOW - timedelta(hours=7),
            note_id="N1",
        )
    ]
    transcript = [
        TranscriptRow(sender_label="Mike", body="7pm works", ts=NOW - timedelta(hours=6))
    ]
    store = FakeStore()

    updated, struck = await strike_unanswered_notes(
        store,
        notes=[_note()],
        loop_decisions=loop_decisions,
        transcript=transcript,
        now=NOW,
    )

    assert struck == ()
    assert updated[0].strikes == 0
    assert store.note_strikes == {}


async def test_struck_note_is_not_eligible_until_revisit_at() -> None:
    struck_note = _note(strikes=1, revisit_at=NOW + timedelta(hours=1))
    assert not eligible_for_raise(struck_note, now=NOW)
    assert eligible_for_raise(struck_note, now=NOW + timedelta(hours=2))


async def test_a_struck_note_is_never_raised_again() -> None:
    """The struck note is filtered out before the model ever sees it — even
    a model that tries to `remind` about it (a hallucinated/replayed id) is
    refused, because it is not in the eligible set this pass was built
    from. This is the one line `eligible_for_raise`/the eligibility filter
    proves by reverting: remove the filter and this test reddens (the
    reminder gets created)."""
    struck = _note(id_="N2", strikes=1, revisit_at=NOW + timedelta(hours=1))
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N2",
                            "action": "remind",
                            "when": "2026-09-12T16:00:00+08:00",
                            "text": "again?",
                        }
                    ]
                }
            )
        ),
    )
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[struck],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.reminders_created == ()
    assert store.reminders == {}
    assert store.decision_rows[0]["verdict"] == "decline"
    assert store.decision_rows[0]["reason"] == R_UNKNOWN_NOTE
    # and the model was never even shown the struck note's id
    prompt = model.calls[0]["prompt"]
    assert "N2" not in prompt


# --- decide: failure modes degrade to decline -----------------------------


async def test_decide_model_timeout_degrades_to_decline() -> None:
    model = FakeModelClient()
    model.script("proactive_decide", FakeCompletion(content="", ok=False, error_kind="timeout"))
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert not outcome.ok
    assert outcome.reminders_created == ()
    assert store.decision_rows[0]["verdict"] == "decline"
    assert store.decision_rows[0]["reason"] == R_MODEL_TIMEOUT


async def test_decide_unparseable_json_degrades_to_decline() -> None:
    model = FakeModelClient()
    model.script("proactive_decide", FakeCompletion(content="not json at all"))
    store = FakeStore()

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert not outcome.ok
    assert store.decision_rows[0]["reason"] == R_UNPARSEABLE


async def test_decide_never_raises_when_the_reminder_insert_fails() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N1",
                            "action": "remind",
                            "when": "2026-09-12T16:00:00+08:00",
                            "text": "ask",
                        }
                    ]
                }
            )
        ),
    )
    store = FakeStore(raise_on_reminder_insert=True)

    outcome = await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert outcome.ok  # the pass itself did not blow up
    assert outcome.reminders_created == ()
    assert store.decision_rows[0]["verdict"] == "failed"


# --- act: firing a due reminder --------------------------------------------


async def test_act_fires_a_due_reminder_speaks_and_resolves_fired() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_act",
        FakeCompletion(content=json.dumps({"speak": True, "text": "hey, where's dinner?"})),
    )
    store = FakeStore()
    _seed_reminder(store)
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.ok
    assert outcome.verdict == "spoke"
    assert len(session.calls) == 1  # deliver actually reached the wire
    spoke_rows = [r for r in store.decision_rows if r["verdict"] == "spoke"]
    assert len(spoke_rows) == 1
    assert spoke_rows[0]["reminder_id"] == "R1"
    assert spoke_rows[0]["note_id"] == "N1"
    assert store.reminders["R1"]["state"] == "done"
    assert store.reminders["R1"]["resolved_by"] == "fired"


async def test_act_delivery_failure_records_failed_not_spoke() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_act",
        FakeCompletion(content=json.dumps({"speak": True, "text": "hey, where's dinner?"})),
    )
    store = FakeStore()
    _seed_reminder(store)
    session = _FakeSession(status=500)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert not outcome.ok
    assert outcome.verdict == "failed"
    assert store.decision_rows[0]["verdict"] == "failed"


# --- act: fail-closed / silence paths ---------------------------------------


async def test_act_model_failure_stays_silent_and_never_delivers() -> None:
    model = FakeModelClient()
    model.script("proactive_act", FakeCompletion(content="", ok=False, error_kind="timeout"))
    store = FakeStore()
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.verdict == "silent"
    assert session.calls == []  # never reached the wire


async def test_act_model_choosing_not_to_speak_is_silent() -> None:
    model = FakeModelClient()
    model.script("proactive_act", FakeCompletion(content=json.dumps({"speak": False})))
    store = FakeStore()
    _seed_reminder(store)
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.verdict == "silent"
    assert session.calls == []
    assert store.reminders["R1"]["resolved_by"] == "fired"


async def test_act_speak_true_but_empty_text_stays_silent() -> None:
    """Ungrounded content is never sent — an empty message body is treated
    the same as choosing not to speak, never coerced into a send."""
    model = FakeModelClient()
    model.script("proactive_act", FakeCompletion(content=json.dumps({"speak": True, "text": ""})))
    store = FakeStore()
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.verdict == "silent"
    assert session.calls == []


async def test_act_unparseable_model_output_stays_silent() -> None:
    model = FakeModelClient()
    model.script("proactive_act", FakeCompletion(content="not json"))
    store = FakeStore()
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask about dinner"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.verdict == "silent"
    assert store.decision_rows[0]["reason"] == R_UNPARSEABLE


async def test_act_refuses_to_speak_without_a_note_to_ground_on() -> None:
    """The brake: a `speak` with no `grounded_on` is a hold (CLAUDE.md).
    An ungrounded reminder (no note_id) must never even reach the model —
    prove this by reverting the `if not reminder.note_id` guard in
    `loop/decide_proactive.py::act`; this test reddens as soon as the model
    gets called (there is nothing scripted for it, so it raises)."""
    model = FakeModelClient()  # nothing scripted — a call here is itself a failure
    store = FakeStore()
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        _config(),
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id=None, due_at=NOW, intent="something vague"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert outcome.verdict == "silent"
    assert outcome.reason == R_NO_GROUNDING
    assert model.calls == []
    assert session.calls == []


async def test_act_with_no_home_room_configured_stays_silent() -> None:
    config = Config(env=Env(), clocks=Clocks(), workspaces={}, rooms={})
    model = FakeModelClient()
    store = FakeStore()
    session = _FakeSession(status=200)

    outcome = await act(
        model,
        store,
        config,
        PeopleDirectory([]),
        reminder=PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N1", due_at=NOW, intent="ask"
        ),
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
        session=session,
    )

    assert not outcome.ok
    assert outcome.reason == R_NO_HOME_ROOM
    assert model.calls == []
    assert session.calls == []


def test_home_room_resolves_the_workspace_label() -> None:
    config = _config(home_label="Signal")
    room = home_room(config)
    assert room is not None
    assert room.platform == "signal"
    assert room.conversation_id == "g1"


def test_home_room_is_none_when_unconfigured() -> None:
    config = Config(env=Env(), clocks=Clocks(), workspaces={}, rooms={})
    assert home_room(config) is None


# --- call_id provenance -----------------------------------------------------


async def test_call_id_reaches_the_reminder_and_the_decision_row() -> None:
    model = FakeModelClient()
    model.script(
        "proactive_decide",
        FakeCompletion(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N1",
                            "action": "remind",
                            "when": "2026-09-12T16:00:00+08:00",
                            "text": "ask",
                        }
                    ]
                }
            ),
            call_id=99,
        ),
    )
    store = FakeStore()

    await decide(
        model,
        store,
        workspace=WORKSPACE,
        platform=PLATFORM,
        conversation_id=CONVERSATION_ID,
        notes=[_note()],
        standing=None,
        loop_decisions=[],
        transcript=[],
        now=NOW,
    )

    assert store.reminders["R1"]["created_call_id"] == 99
    assert store.decision_rows[0]["call_id"] == 99


# --- pure helpers ------------------------------------------------------------


def test_has_been_answered_true_for_a_human_row_after_the_raise() -> None:
    spoken_at = NOW - timedelta(hours=1)
    transcript = [TranscriptRow(sender_label="Mike", body="ok", ts=NOW - timedelta(minutes=30))]
    assert has_been_answered(spoken_at, transcript)


def test_has_been_answered_false_when_only_ora_spoke_after() -> None:
    spoken_at = NOW - timedelta(hours=1)
    transcript = [
        TranscriptRow(
            sender_label="Ora", body="following up", ts=NOW - timedelta(minutes=30), is_ora=True
        )
    ]
    assert not has_been_answered(spoken_at, transcript)


def test_eligible_for_raise_true_for_a_never_struck_note() -> None:
    assert eligible_for_raise(_note(), now=NOW)
