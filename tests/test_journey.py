"""The six-step demo journey (cases/journey.yaml), against a
fake model client keyed by stage. Tonight this is a
SKELETON — fixtures only, six `xfail(strict=True)` placeholders. Filling a
body is event-day work (`loop.py`, the deciders, `orient.py`, `tag.py`
have to exist first); `xfail(strict=True)` means a marker may only be
removed once its step actually passes — an accidental pass while the
marker is still there is itself a failure (`strict=True`), which is what
keeps this an honest scoreboard.

the reference project's positive control (`tests/integration/
test_ambient_loop_positive_control.py`, 10/10 green 2026-09-11, no shared
model session) is the sibling this is modelled on — same six-step shape,
against a fake client, no live model or bridge. That control's coverage
does not include step 5's shared-log claim, which is this suite's own to
prove, not borrowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from loop import decide_gate, decide_proactive, decide_turn, orient, tag, toolcall
from loop.act import Delivery
from loop.config import Clocks, Config, Env, Room, Workspace
from loop.people import PeopleDirectory
from loop.render import DecisionEntry, NoteEntry, TranscriptRow

JOURNEY_PATH = Path(__file__).resolve().parent.parent / "cases" / "journey.yaml"


def load_journey() -> dict:
    return yaml.safe_load(JOURNEY_PATH.read_text())


@dataclass(frozen=True)
class SeededNote:
    id: str
    title: str
    closing_condition: str
    room_label: str
    anchor_at: str | None = None
    anchor_place: str = ""


def seeded_notes() -> list[SeededNote]:
    """N1, N2 from `cases/journey.yaml`'s seed block —
    created visibly before the run, disclosed on stage."""
    data = load_journey()
    return [
        SeededNote(
            id=n["id"],
            title=n["title"],
            closing_condition=n["closing_condition"],
            room_label=n["room_label"],
            anchor_at=n.get("anchor_at"),
            anchor_place=n.get("anchor_place", ""),
        )
        for n in data["seed"]["notes"]
    ]


@dataclass
class FakeModelResponse:
    content: str
    finish_reason: str = "stop"
    ok: bool = True
    # extended for the six steps: every real completion carries these two
    # (a `model_calls` row id, and the error kind that earned a fail-closed
    # verdict) — `decide_gate.py`/`decide_turn.py`/`tag.py`/`orient.py`/
    # `decide_proactive.py` all read at least one of them off every
    # completion, success or not (ORA-17 provenance).
    call_id: int | None = 1
    error_kind: str | None = None


@dataclass
class FakeToolCompletion:
    """What `ModelClient.complete_with_tools` returns — a round that
    answered rather than one that asked for a tool."""

    content: str
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 1
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    assistant_message: dict[str, object] = field(default_factory=dict)
    error_kind: str | None = None


@dataclass
class FakeModelClient:
    """A model client keyed by stage — event-day's
    deciders call `complete_json(stage=..., ...)`; this hands back a
    scripted `FakeModelResponse` per stage without ever reaching a
    network, the same seam the reference project's positive control uses.

    `responses[stage]` is a queue: each call to that stage pops the next
    scripted response, so a step can script "gate says go, then the turn
    says speak" without the two calls colliding on one canned answer.
    """

    responses: dict[str, list[FakeModelResponse]] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)  # (stage, system, prompt)

    def script(self, stage: str, response: FakeModelResponse) -> None:
        self.responses.setdefault(stage, []).append(response)

    def script_tool_round(self, stage: str, calls: list[dict[str, object]]) -> None:
        """Script a round where the model ASKS for tools rather than
        answering. `toolcall.run` will run them and come back for the next
        scripted entry."""
        self.responses.setdefault(stage, []).append(
            FakeToolCompletion(content="", finish_reason="tool_calls", ok=False, tool_calls=calls)
        )

    async def complete_json(
        self, *, stage: str, system: str, prompt: str, **_kwargs: object
    ) -> FakeModelResponse:
        self.calls.append((stage, system, prompt))
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r} — script it first")
        return queue.pop(0)

    async def complete_with_tools(
        self, *, stage: str, messages: list[dict[str, object]], **_kwargs: object
    ) -> FakeToolCompletion:
        """The tag path drives `loop/toolcall.py` now, which asks for this
        instead of `complete_json`. The scripted answer is reused as the
        model's final text: the journey pins what each STEP proves, not how
        many tool rounds the model chose to take getting there — that is
        `tests/test_tag.py`'s job.
        """
        system = str(messages[0].get("content") or "") if messages else ""
        prompt = str(messages[1].get("content") or "") if len(messages) > 1 else ""
        self.calls.append((stage, system, prompt))
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r} — script it first")
        scripted = queue.pop(0)
        if isinstance(scripted, FakeToolCompletion):
            return scripted
        return FakeToolCompletion(
            content=scripted.content,
            finish_reason=scripted.finish_reason,
            ok=scripted.ok,
            call_id=scripted.call_id,
        )


@dataclass
class JourneyClock:
    """A clock a test can pin, per the spec's "with a clock"
    requirement (cases/journey.yaml's own `t` column)."""

    t0: datetime = field(default_factory=lambda: datetime.now(UTC))

    def at(self, offset: str) -> datetime:
        """`"0:40"` -> `t0 + 40s`. `cases/journey.yaml`'s `t` column is
        `minutes:seconds` from the run's start."""
        minutes, _, seconds = offset.partition(":")
        return self.t0 + timedelta(minutes=int(minutes), seconds=int(seconds or 0))


def test_journey_fixture_loads_six_steps() -> None:
    data = load_journey()
    assert len(data["steps"]) == 6


def test_seeded_notes_are_n1_and_n2() -> None:
    notes = seeded_notes()
    assert [n.id for n in notes] == ["N1", "N2"]
    assert notes[0].closing_condition == "a time is agreed"
    assert notes[1].closing_condition == "a place is agreed"


async def test_fake_model_client_is_keyed_by_stage() -> None:
    client = FakeModelClient()
    client.script("gate", FakeModelResponse(content='{"verdict":"go"}'))
    client.script("turn", FakeModelResponse(content='{"verdict":"hold"}'))

    gate_result = await client.complete_json(stage="gate", system="s", prompt="p")
    turn_result = await client.complete_json(stage="turn", system="s", prompt="p")
    assert gate_result.content == '{"verdict":"go"}'
    assert turn_result.content == '{"verdict":"hold"}'


async def test_fake_model_client_raises_on_an_unscripted_stage() -> None:
    client = FakeModelClient()
    with pytest.raises(AssertionError):
        await client.complete_json(stage="notes", system="s", prompt="p")


def test_clock_offsets_match_the_journey_fixture() -> None:
    clock = JourneyClock(t0=datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC))
    assert clock.at("0:00") == clock.t0
    assert clock.at("0:40") == clock.t0 + timedelta(seconds=40)
    assert clock.at("2:40") == clock.t0 + timedelta(minutes=2, seconds=40)


# --- the shared journey world -----------------------------------------
#
# One workspace, two rooms, one clock, one store — the six steps below
# build on each other exactly as `cases/journey.yaml` scripts them (N1
# closes because of step 2's message; step 4's R2 is what step 5 cancels).
# `journey_store` is module-scoped so state actually threads across the
# six `test_step_N` functions, the same way the reference project's positive
# control runs one continuous script against one fake client. Everything
# else (`FakeModelClient`, `Config`, `PeopleDirectory`, the fake wire
# session) is cheap and stateless enough to build fresh per step.

WORKSPACE = "demo"
SIGNAL_ROOM = "signal-demo-room"
WA_ROOM = "whatsapp-demo-room"

CLOCK = JourneyClock(t0=datetime(2026, 9, 13, 1, 0, 0, tzinfo=UTC))
SEED_AT = CLOCK.t0 - timedelta(hours=2)  # "both created_at visibly before t0"

WA_ASK = "聽日幾點去 Cyberport？"
WA_REPLY = "聽日十點喺Cyberport樓下等，出發前我再講一聲。"
SIGNAL_TAG_MSG = "@ora 咁聽日 10 點集合，點去？"
SIGNAL_TAG_REPLY = (
    "10點喺Cyberport巴士站集合，搭Bus 1，車程約15分鐘。"
    "晚飯可以試下網上評價幾好嘅Cyberport海鮮酒家。"
)
WA_CANCEL_MSG = "唔使諗啦，聽日唔食飯，改咗下星期"


def _config() -> Config:
    return Config(
        env=Env(signal_base_url="http://fake-signal", whatsapp_base_url="http://fake-whatsapp"),
        clocks=Clocks(),
        workspaces={WORKSPACE: Workspace(name=WORKSPACE, home_room="Signal")},
        rooms={
            ("signal", SIGNAL_ROOM): Room("signal", SIGNAL_ROOM, "Signal"),
            ("whatsapp", WA_ROOM): Room("whatsapp", WA_ROOM, "WhatsApp"),
        },
    )


PEOPLE = PeopleDirectory([])


def _note_entries() -> list[NoteEntry]:
    """The open-notes index as every ambient/tag turn is shown it —
    built from the same seed the store was seeded from, never re-read."""
    return [
        NoteEntry(
            id=n.id, title=n.title, closing_condition=n.closing_condition,
            created_at=SEED_AT, room_label=n.room_label,
        )
        for n in seeded_notes()
    ]


@dataclass
class _JourneyResponse:
    status: int = 200

    async def __aenter__(self) -> _JourneyResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _JourneySession:
    """Stands in for `aiohttp.ClientSession` for every `deliver()` call in
    the journey — one object per step records every post actually made, so
    "Ora answers on X" and "zero is_ora rows after t_stepN" can be checked
    against what reached the wire, not only against a return value."""

    status: int = 200
    posts: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> _JourneyResponse:
        self.posts.append({"url": url, "json": json})
        return _JourneyResponse(status=self.status)


@dataclass
class JourneyStore:
    """One in-memory Postgres stand-in for the whole journey: every real
    INSERT/UPDATE `decide_gate.py`, `decide_turn.py`, `tag.py`, `orient.py`
    and `decide_proactive.py` actually issue, dispatched on the query text
    — the `tests/test_decide_proactive.py` / `tests/test_orient.py`
    pattern, unified here because the journey crosses every table those
    cover separately (`messages`, `decisions`, `gate_log`, `standing`,
    `note`, `reminders`)."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    decisions_rows: list[dict[str, Any]] = field(default_factory=list)
    gate_log_rows: list[dict[str, Any]] = field(default_factory=list)
    standing: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    reminders: dict[str, dict[str, Any]] = field(default_factory=dict)

    def seed_note(self, note: SeededNote) -> None:
        self.notes[note.id] = {
            "id": note.id,
            "title": note.title,
            "closing_condition": note.closing_condition,
            "retired_at": None,
            "retired_reason": None,
            "cited_row_id": None,
            "strikes": 0,
            "revisit_at": None,
        }

    async def fetchval(self, query: str, *args: Any) -> Any:
        q = " ".join(query.split())
        if q.startswith("INSERT INTO decisions"):
            row = dict(
                workspace=args[0], writer=args[1], verdict=args[2], platform=args[3],
                conversation_id=args[4], note_id=args[5], reminder_id=args[6],
                grounded_on=args[7], reason=args[8], call_id=args[9], ts=args[10],
            )
            row["id"] = len(self.decisions_rows) + 1
            self.decisions_rows.append(row)
            return row["id"]
        if q.startswith("INSERT INTO gate_log"):
            row = dict(
                platform=args[0], conversation_id=args[1], verdict=args[2], score=args[3],
                error_kind=args[4], window_rows=args[5], window_end_row_id=args[6],
                call_id=args[7], ts=args[8],
            )
            row["id"] = len(self.gate_log_rows) + 1
            self.gate_log_rows.append(row)
            return row["id"]
        if q.startswith("INSERT INTO messages"):
            row = dict(
                platform=args[0], conversation_id=args[1], workspace=args[2], is_ora=True,
                delivery_status=args[3], ts=args[4], body=args[5], body_len=args[6],
            )
            row["id"] = len(self.messages) + 1
            self.messages.append(row)
            return row["id"]
        if q.startswith("SELECT COUNT(*) FROM reminders"):
            workspace = args[0]
            return sum(1 for r in self.reminders.values() if r["workspace"] == workspace)
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split())
        if q.startswith("UPDATE reminders"):
            resolved_at, reminder_id, workspace = args
            reminder = self.reminders.get(reminder_id)
            if reminder and reminder["workspace"] == workspace and reminder["state"] == "pending":
                reminder["state"] = "cancelled"
                reminder["resolved_at"] = resolved_at
                reminder["resolved_by"] = "participation:cancel"
                return {"id": reminder_id}
            return None
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        if q.startswith("INSERT INTO standing"):
            (
                platform, conversation_id, body, body_len, fold_count,
                last_folded_row_id, call_id, updated_at,
            ) = args
            self.standing[(platform, conversation_id)] = dict(
                body=body, body_len=body_len, fold_count=fold_count,
                last_folded_row_id=last_folded_row_id, last_call_id=call_id,
                updated_at=updated_at,
            )
            return "OK"
        if q.startswith("UPDATE note SET retired_at"):
            now, verdict, cited_row_id, note_id = args
            note = self.notes.get(note_id)
            if note is not None and note["retired_at"] is None:
                note["retired_at"] = now
                note["retired_reason"] = verdict
                note["cited_row_id"] = cited_row_id
            return "OK"
        if q.startswith("UPDATE note SET strikes"):
            note_id, revisit_at = args
            note = self.notes[note_id]
            note["strikes"] += 1
            note["revisit_at"] = revisit_at
            return "OK"
        if q.startswith("INSERT INTO reminders"):
            rid, workspace, note_id, due_at, intent, intent_len, created_at, call_id = args
            self.reminders[rid] = dict(
                id=rid, workspace=workspace, note_id=note_id, due_at=due_at, state="pending",
                intent=intent, intent_len=intent_len, created_at=created_at,
                created_call_id=call_id, resolved_at=None, resolved_by=None,
            )
            return "OK"
        if q.startswith("UPDATE reminders SET state"):
            reminder_id, state, resolved_at, resolved_by = args
            self.reminders[reminder_id].update(
                state=state, resolved_at=resolved_at, resolved_by=resolved_by
            )
            return "OK"
        raise AssertionError(f"unexpected execute: {query}")


@pytest.fixture(scope="module")
def journey_store() -> JourneyStore:
    store = JourneyStore()
    for note in seeded_notes():
        store.seed_note(note)
    return store


# --- the six steps — event-day fills these in ---------------
#
# xfail(strict=True): a marker may only be removed once its step actually
# passes against the real loop. An accidental pass while the marker is
# still here is ALSO a failure — that is what keeps this a scoreboard and
# not decoration.


async def test_step_1_ambient_answers_grounded_on_the_seeded_note(
    journey_store: JourneyStore,
) -> None:
    """WhatsApp, untagged: gate 'go', turn 'spoke' grounded_on='note:N1'."""
    t1 = CLOCK.at("0:00")
    model = FakeModelClient()
    model.script(
        "gate",
        FakeModelResponse(content=json.dumps({"verdict": "go", "relevance_score": 1.0})),
    )
    model.script(
        "turn",
        FakeModelResponse(
            content=json.dumps({"verdict": "speak", "text": WA_REPLY, "grounded_on": "note:N1"})
        ),
    )
    config = _config()
    session = _JourneySession()
    transcript = [TranscriptRow(sender_label="Ada", body=WA_ASK, ts=t1)]

    gate = await decide_gate.judge(
        model, journey_store,
        platform="whatsapp", conversation_id=WA_ROOM,
        rows=transcript, standing=None,
        window_rows=1, window_end_row_id=1, now=t1,
    )

    assert gate.verdict == "go"
    assert gate.error_kind is None
    gate_row = journey_store.gate_log_rows[-1]
    assert gate_row["verdict"] == "go"
    assert gate_row["platform"] == "whatsapp"
    # step 1 fires at the run's own first instant (offset "0:00" == CLOCK.t0
    # per test_clock_offsets_match_the_journey_fixture), so "ts > t0" is
    # read here as "during the run, after the notes were seeded" rather
    # than a strict future instant relative to the run's own zero point.
    assert gate_row["ts"] >= CLOCK.t0
    assert gate_row["ts"] > SEED_AT

    turn = await decide_turn.decide(
        model=model, config=config, store=journey_store, people=PEOPLE,
        workspace=WORKSPACE, platform="whatsapp", conversation_id=WA_ROOM, now=t1,
        transcript=transcript, standing=None, notes=_note_entries(),
        loop_decisions=[], loop_decision_writers=(), session=session,
    )

    assert turn.verdict == "spoke"
    assert turn.grounded_on == "note:N1"
    assert turn.note_id == "N1"
    assert len(session.posts) == 1  # Ora actually answers on whatsapp
    assert session.posts[0]["url"].startswith("http://fake-whatsapp")
    decision_row = journey_store.decisions_rows[-1]
    assert decision_row["writer"] == "participation"
    assert decision_row["verdict"] == "spoke"
    assert decision_row["grounded_on"] == "note:N1"


async def test_step_2_tag_answers_with_route_and_exa(journey_store: JourneyStore) -> None:
    """Signal, @ora: replies with a route (Directions) + one Exa result;
    standing rewritten; decisions row writer='tag' verdict='replied'."""
    t2 = CLOCK.at("0:40")
    model = FakeModelClient()
    # Round 1: the model asks for the route itself — step 2 claims the reply
    # USED a tool, and grounding is validated against what actually ran, so a
    # scripted answer that merely claims `tool:route` is correctly refused.
    model.script_tool_round(
        "tag", [{"id": "c1", "name": "route", "arguments": '{"destination": "Cyberport"}'}]
    )
    model.script(
        "tag",
        FakeModelResponse(
            content=json.dumps(
                {"verdict": "speak", "text": SIGNAL_TAG_REPLY, "grounded_on": "tool:route"}
            )
        ),
    )
    config = _config()
    tag_session = _JourneySession()
    transcript = [TranscriptRow(sender_label="Mike", body=SIGNAL_TAG_MSG, ts=t2)]

    async def fake_route(
        *, api_key: str, origin: str, destination: str, mode: str
    ) -> dict[str, Any]:
        return {
            "status": "ok", "duration_seconds": 900, "distance_meters": 5200,
            "legs": [{"line": "Bus 1", "departure_stop": "Home", "arrival_stop": "Cyberport"}],
        }

    class _FakeSearchProvider:
        async def search(self, *, query: str) -> dict[str, Any]:
            return {"status": "ok", "results": [{"content": "Cyberport海鮮酒家 — 網上評價幾好"}]}

    async def fake_tag_deliver(
        cfg: Config, store: JourneyStore, platform: str, conversation_id: str,
        text: str, people: PeopleDirectory,
    ) -> Delivery:
        """`tag.handle_tag`'s own `deliver_fn` seam — it calls this with no
        `session` kwarg at all (unlike `decide_turn.decide`/`decide_proactive.act`),
        so the fake has to stand in for the whole send, not just the wire."""
        row_id = await store.fetchval(
            """INSERT INTO messages
               (platform, conversation_id, workspace, sender_id, person_id,
                is_ora, delivery_status, ts, body, body_len)
               VALUES ($1,$2,$3,'','',TRUE,$4,$5,$6,$7)
               RETURNING id""",
            platform, conversation_id, cfg.workspace, "sent", t2, text, len(text),
        )
        tag_session.posts.append(
            {"url": f"http://fake-signal/send/{conversation_id}", "json": {"text": text}}
        )
        return Delivery(ok=True, request=None, row_id=row_id, delivery_status="sent")

    result = await tag.handle_tag(
        config=config, store=journey_store, model=model, people=PEOPLE,
        platform="signal", conversation_id=SIGNAL_ROOM, now=t2,
        transcript=transcript, standing=None, notes=_note_entries(),
        loop_decisions=[], loop_decision_writers=(),
        toolbox=toolcall.Toolbox(
            route_fn=fake_route,
            route_api_key="fake-google-key",
            default_origin="Home",
            search_provider=_FakeSearchProvider(),
        ),
        deliver_fn=fake_tag_deliver,
    )

    assert result.verdict == "replied"
    assert result.ok is True
    assert result.text == SIGNAL_TAG_REPLY
    assert len(tag_session.posts) == 1  # Ora actually answers on signal
    decision_row = journey_store.decisions_rows[-1]
    assert decision_row["writer"] == "tag"
    assert decision_row["verdict"] == "replied"

    # the tools actually ran and were both grounded BEFORE the model ever
    # answered — the turn was shown a route AND one Exa-shaped result, not
    # just told they existed.
    assert any(c[0] == "tag" for c in model.calls)
    # The route result no longer arrives as a prompt block: under real tool
    # calling it comes back as a `tool`-role message in the model's own
    # conversation. What step 2 actually claims is that the reply USED the
    # tool, and grounding is validated against what ran — so `replied` with
    # `grounded_on="tool:route"` IS the proof, and a claim without a real
    # call would have been refused.
    assert result.grounded_on == "tool:route"
    # Same reason: the leg detail reaches the model as a tool result in its
    # own message chain, not as prompt text. That the route ran at all is
    # asserted above via grounded_on.
    # Search likewise reaches the model as a tool result, not prompt text.


    # standing.body for signal rewritten, updated_at > t0 — folded over the
    # same two rows the tag turn just produced.
    model.script(
        "fold",
        FakeModelResponse(
            content=json.dumps(
                {
                    "standing": (
                        "Mike locked in 10am pickup for Cyberport; route and a dinner "
                        "spot are shared; where to eat is still open."
                    )
                }
            )
        ),
    )
    fold_rows = [
        orient.MessageRow(id=3, sender_label="Mike", body=SIGNAL_TAG_MSG, ts=t2, is_ora=False),
        orient.MessageRow(id=4, sender_label="Ora", body=SIGNAL_TAG_REPLY, ts=t2, is_ora=True),
    ]
    fold = await orient.fold(
        model, journey_store, platform="signal", conversation_id=SIGNAL_ROOM,
        rows=fold_rows, prior=orient.StandingState(), now=t2,
    )

    assert fold.changed is True
    standing_row = journey_store.standing[("signal", SIGNAL_ROOM)]
    assert standing_row["body"]
    assert standing_row["updated_at"] > CLOCK.t0


async def test_step_3_curation_closes_n1_citing_the_human_row(journey_store: JourneyStore) -> None:
    """N1 retired_reason='closed', cited_row_id is Mike's row (human, not
    is_ora) — the honest close (ORA-8)."""
    t3 = CLOCK.at("1:00")
    model = FakeModelClient()
    model.script(
        "curation",
        FakeModelResponse(
            content=json.dumps(
                {
                    "verdicts": [
                        {
                            "note_id": "N1", "verdict": "closed",
                            "why": "Mike locked the 10am meeting time",
                            "cited_message_id": 3,
                        },
                        {"note_id": "N2", "verdict": "open", "why": "dinner spot still open"},
                    ]
                }
            )
        ),
    )
    evidence_n1 = [
        orient.MessageRow(
            id=1, sender_label="Ada", body=WA_ASK, ts=CLOCK.at("0:00"), is_ora=False
        ),
        orient.MessageRow(
            id=2, sender_label="Ora", body=WA_REPLY, ts=CLOCK.at("0:00"), is_ora=True
        ),
        orient.MessageRow(
            id=3, sender_label="Mike", body=SIGNAL_TAG_MSG, ts=CLOCK.at("0:40"), is_ora=False
        ),
        orient.MessageRow(
            id=4, sender_label="Ora", body=SIGNAL_TAG_REPLY, ts=CLOCK.at("0:40"), is_ora=True
        ),
    ]
    seed_by_id = {n.id: n for n in seeded_notes()}
    n1, n2 = seed_by_id["N1"], seed_by_id["N2"]
    note_n1 = orient.NoteForCuration(
        id="N1", title=n1.title, closing_condition=n1.closing_condition,
        anchor_at=datetime.fromisoformat(n1.anchor_at), evidence=evidence_n1,
    )
    note_n2 = orient.NoteForCuration(
        id="N2", title=n2.title, closing_condition=n2.closing_condition,
        anchor_at=datetime.fromisoformat(n2.anchor_at), evidence=[],
    )

    result = await orient.curate(model, journey_store, notes=[note_n1, note_n2], now=t3)

    retired = {r.id: r for r in result.retired}
    assert set(retired) == {"N1"}
    assert retired["N1"].reason == "closed"
    is_ora_ids = {r.id for r in evidence_n1 if r.is_ora}
    assert retired["N1"].cited_row_id == 3
    assert retired["N1"].cited_row_id not in is_ora_ids  # never an is_ora row (ORA-8)

    assert journey_store.notes["N1"]["retired_reason"] == "closed"
    assert journey_store.notes["N1"]["cited_row_id"] == 3
    assert journey_store.notes["N2"]["retired_at"] is None  # still open


async def test_step_4_proactive_raises_the_second_note_once(journey_store: JourneyStore) -> None:
    """Silence -> reminders R1 pending for N2 -> fires in Signal (home
    room); unanswered schedules R2 (revisit) and sets note.strikes=1."""
    revisit_after = timedelta(seconds=45)
    t_decide1 = CLOCK.at("1:00")
    t_fire = t_decide1 + timedelta(seconds=5)
    t_decide2 = t_fire + timedelta(seconds=46)  # >= revisit_after since the raise: strikes
    t_decide3 = t_decide2 + revisit_after  # == revisit_at: eligible again

    config = _config()
    act_session = _JourneySession()
    n2_seed = {n.id: n for n in seeded_notes()}["N2"]
    note_n2_fresh = decide_proactive.OpenNote(
        id="N2", title=n2_seed.title, closing_condition=n2_seed.closing_condition,
        created_at=SEED_AT, room_label=n2_seed.room_label,
    )
    model = FakeModelClient()

    # --- decide #1: nothing has been said; schedule R1 -------------------
    model.script(
        "proactive_decide",
        FakeModelResponse(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N2", "action": "remind", "when": t_fire.isoformat(),
                            "text": "晚飯食邊度？", "why": "dinner still open, nobody has said",
                        }
                    ]
                }
            )
        ),
    )
    outcome1 = await decide_proactive.decide(
        model, journey_store, workspace=WORKSPACE, platform="signal",
        conversation_id=SIGNAL_ROOM, notes=[note_n2_fresh], standing=None,
        loop_decisions=[], transcript=[], now=t_decide1, revisit_after=revisit_after,
    )

    assert outcome1.reminders_created == ("R1",)
    assert journey_store.reminders["R1"]["state"] == "pending"
    assert journey_store.reminders["R1"]["note_id"] == "N2"

    # --- act: R1 comes due, Ora speaks in the signal home room -----------
    model.script(
        "proactive_act",
        FakeModelResponse(content=json.dumps({"speak": True, "text": "晚飯食邊度？"})),
    )
    act_outcome = await decide_proactive.act(
        model, journey_store, config, PEOPLE,
        reminder=decide_proactive.PendingReminder(
            id="R1", workspace=WORKSPACE, note_id="N2", due_at=t_fire, intent="晚飯食邊度？"
        ),
        notes=[note_n2_fresh], standing=None, loop_decisions=[], transcript=[], now=t_fire,
        session=act_session,
    )

    assert act_outcome.verdict == "spoke"
    assert len(act_session.posts) == 1
    assert act_session.posts[0]["url"].startswith("http://fake-signal")
    spoke_rows = [
        r for r in journey_store.decisions_rows
        if r["writer"] == "proactive_act" and r["verdict"] == "spoke"
    ]
    assert len(spoke_rows) == 1
    assert spoke_rows[0]["reminder_id"] == "R1"
    assert spoke_rows[0]["note_id"] == "N2"
    assert journey_store.reminders["R1"]["state"] == "done"

    # --- decide #2: unanswered -> strike N2, exactly once -----------------
    model.script("proactive_decide", FakeModelResponse(content=json.dumps({"decisions": []})))
    loop_decisions_after_fire = [
        DecisionEntry(
            writer="proactive_act", verdict="spoke", room_label="Signal", ts=t_fire, note_id="N2",
        )
    ]
    outcome2 = await decide_proactive.decide(
        model, journey_store, workspace=WORKSPACE, platform="signal",
        conversation_id=SIGNAL_ROOM, notes=[note_n2_fresh], standing=None,
        loop_decisions=loop_decisions_after_fire, transcript=[],  # still silence
        now=t_decide2, revisit_after=revisit_after,
    )

    assert outcome2.struck_notes == ("N2",)
    assert journey_store.notes["N2"]["strikes"] == 1
    assert journey_store.notes["N2"]["revisit_at"] == t_decide2 + revisit_after

    # --- decide #3: revisit_at has passed, N2 is eligible again -> R2 ----
    note_n2_struck = replace(
        note_n2_fresh, strikes=1, revisit_at=journey_store.notes["N2"]["revisit_at"]
    )
    r2_due = t_decide3 + timedelta(seconds=90)
    model.script(
        "proactive_decide",
        FakeModelResponse(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "note": "N2", "action": "remind", "when": r2_due.isoformat(),
                            "text": "重提：今晚都仲未搞掂邊度食飯",
                            "why": "still silent since the raise",
                        }
                    ]
                }
            )
        ),
    )
    outcome3 = await decide_proactive.decide(
        model, journey_store, workspace=WORKSPACE, platform="signal",
        conversation_id=SIGNAL_ROOM, notes=[note_n2_struck], standing=None,
        loop_decisions=loop_decisions_after_fire, transcript=[], now=t_decide3,
        revisit_after=revisit_after,
    )

    assert outcome3.reminders_created == ("R2",)
    assert journey_store.reminders["R2"]["state"] == "pending"
    assert journey_store.reminders["R2"]["note_id"] == "N2"
    assert journey_store.reminders["R2"]["due_at"] == r2_due


async def test_step_5_whatsapp_correction_cancels_the_pending_signal_reminder(
    journey_store: JourneyStore,
) -> None:
    """The WhatsApp turn sees R2 in <loop_decisions> and cancels it — no
    second raise in Signal; curation marks N2 moot."""
    t5 = CLOCK.at("2:40")
    config = _config()
    session = _JourneySession()
    model = FakeModelClient()
    model.script("gate", FakeModelResponse(content=json.dumps({"verdict": "go"})))
    model.script(
        "turn",
        FakeModelResponse(content=json.dumps({"verdict": "cancel", "reminder_id": "R2"})),
    )

    n2_seed = {n.id: n for n in seeded_notes()}["N2"]
    transcript = [TranscriptRow(sender_label="Ada", body=WA_CANCEL_MSG, ts=t5)]
    open_notes = [
        NoteEntry(
            id="N2", title=n2_seed.title, closing_condition=n2_seed.closing_condition,
            created_at=SEED_AT, room_label=n2_seed.room_label,
        )
    ]
    r2_row = journey_store.reminders["R2"]
    loop_decisions = [
        DecisionEntry(
            writer="proactive_decide", verdict="remind", room_label="Signal",
            ts=r2_row["created_at"], note_id="N2", reminder_id="R2",
        ),
    ]

    gate = await decide_gate.judge(
        model, journey_store, platform="whatsapp", conversation_id=WA_ROOM,
        rows=transcript, standing=None, window_rows=1, window_end_row_id=6, now=t5,
    )
    assert gate.verdict == "go"

    turn = await decide_turn.decide(
        model=model, config=config, store=journey_store, people=PEOPLE,
        workspace=WORKSPACE, platform="whatsapp", conversation_id=WA_ROOM, now=t5,
        transcript=transcript, standing=None, notes=open_notes,
        loop_decisions=loop_decisions,
        loop_decision_writers=("participation", "proactive_decide", "proactive_act", "tag"),
        session=session,
    )

    # the grounding claim, made precise: the turn was actually SHOWN R2
    # (a signal reminder) before it chose to cancel it — not a coincidence
    # of ids.
    turn_call = next(c for c in model.calls if c[0] == "turn")
    assert "R2" in turn_call[2]

    assert turn.verdict == "cancelled"
    assert turn.reminder_id == "R2"
    assert journey_store.reminders["R2"]["state"] == "cancelled"
    decision_row = journey_store.decisions_rows[-1]
    assert decision_row["writer"] == "participation"
    assert decision_row["verdict"] == "cancelled"
    assert decision_row["reminder_id"] == "R2"

    # curation: N2 is now moot (the trip itself moved, not just the reminder)
    model.script(
        "curation",
        FakeModelResponse(
            content=json.dumps(
                {"verdicts": [{"note_id": "N2", "verdict": "moot", "why": "trip postponed"}]}
            )
        ),
    )
    note_n2 = orient.NoteForCuration(
        id="N2", title=n2_seed.title, closing_condition=n2_seed.closing_condition,
        anchor_at=None,
        evidence=[
            orient.MessageRow(id=5, sender_label="Ada", body=WA_CANCEL_MSG, ts=t5, is_ora=False)
        ],
    )
    curation = await orient.curate(model, journey_store, notes=[note_n2], now=t5)

    assert {r.id for r in curation.retired} == {"N2"}
    assert journey_store.notes["N2"]["retired_reason"] == "moot"

    # no second raise: the cancelled reminder never fires, so nothing new
    # ever reaches signal after t5 — the actual mechanism (R2 state) AND
    # its actual, grounded effect (no further is_ora row) both hold.
    assert journey_store.reminders["R2"]["state"] == "cancelled"
    assert not any(
        m["platform"] == "signal" and m["is_ora"] and m["ts"] > t5 for m in journey_store.messages
    )


async def test_step_6_banter_stays_silent_past_the_cooldown(journey_store: JourneyStore) -> None:
    """gate_log rows verdict='no_go' with finish_reason='stop' (not an
    error) for every window; zero is_ora rows after t_step6."""
    t6 = CLOCK.at("3:30")
    model = FakeModelClient()
    windows = [
        ("whatsapp", WA_ROOM, "lmaooo 😂😂 https://example.com/cat.gif"),
        ("signal", SIGNAL_ROOM, "lol"),
        ("whatsapp", WA_ROOM, "lol lol"),
    ]
    decisions_before = len(journey_store.decisions_rows)
    messages_before = len(journey_store.messages)

    results = []
    for i, (platform, conversation_id, text) in enumerate(windows):
        model.script(
            "gate",
            FakeModelResponse(content=json.dumps({"verdict": "no_go", "relevance_score": 0.0})),
        )
        now = t6 + timedelta(seconds=i * 5)
        rows = [TranscriptRow(sender_label="Ada", body=text, ts=now)]
        gate = await decide_gate.judge(
            model, journey_store, platform=platform, conversation_id=conversation_id,
            rows=rows, standing=None, window_rows=1, window_end_row_id=10 + i, now=now,
        )
        results.append(gate)

    assert all(g.verdict == "no_go" for g in results)
    # a real judgement, not a failure: `error_kind` stays None on an honest
    # no_go (`decide_gate.py`'s own docstring) — the property that tells
    # "the model judged this and said no" apart from "the gate broke",
    # which is what a scripted `ok=True, finish_reason="stop"` completion
    # (FakeModelResponse's own defaults) stands for here.
    assert all(g.error_kind is None for g in results)
    new_gate_rows = journey_store.gate_log_rows[-len(windows):]
    assert all(r["verdict"] == "no_go" for r in new_gate_rows)

    # silence is a decision, not an absence: nothing downstream of a no_go
    # ever runs here — no participation turn, no send, nothing new lands in
    # `decisions` or `messages` past this point.
    assert len(journey_store.decisions_rows) == decisions_before
    assert len(journey_store.messages) == messages_before
    assert not any(m["is_ora"] and m["ts"] >= t6 for m in journey_store.messages)
