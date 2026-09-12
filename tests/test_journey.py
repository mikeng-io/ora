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

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

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

    async def complete_json(
        self, *, stage: str, system: str, prompt: str, **_kwargs: object
    ) -> FakeModelResponse:
        self.calls.append((stage, system, prompt))
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r} — script it first")
        return queue.pop(0)


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


# --- the six steps — event-day fills these in ---------------
#
# xfail(strict=True): a marker may only be removed once its step actually
# passes against the real loop. An accidental pass while the marker is
# still here is ALSO a failure — that is what keeps this a scoreboard and
# not decoration.


@pytest.mark.xfail(strict=True, reason="event-day: loop.py, decide_gate.py, decide_turn.py")
def test_step_1_ambient_answers_grounded_on_the_seeded_note() -> None:
    """WhatsApp, untagged: gate 'go', turn 'spoke' grounded_on='note:N1'."""
    raise NotImplementedError


@pytest.mark.xfail(
    strict=True,
    reason="event-day: tag.py, loop/act.py policy half, loop/route.py + loop/search.py wiring",
)
def test_step_2_tag_answers_with_route_and_exa() -> None:
    """Signal, @ora: replies with a route (Directions) + one Exa result;
    standing rewritten; decisions row writer='tag' verdict='replied'."""
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason="event-day: orient.py curation")
def test_step_3_curation_closes_n1_citing_the_human_row() -> None:
    """N1 retired_reason='closed', cited_row_id is Mike's row (human, not
    is_ora) — the honest close (ORA-8)."""
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason="event-day: decide_proactive.py, loop.py proactive tick")
def test_step_4_proactive_raises_the_second_note_once() -> None:
    """Silence -> reminders R1 pending for N2 -> fires in Signal (home
    room); unanswered schedules R2 (revisit) and sets note.strikes=1."""
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason="event-day: decide_turn.py cancel(reminder_id), ORA-15")
def test_step_5_whatsapp_correction_cancels_the_pending_signal_reminder() -> None:
    """The WhatsApp turn sees R2 in <loop_decisions> and cancels it — no
    second raise in Signal; curation marks N2 moot."""
    raise NotImplementedError


@pytest.mark.xfail(strict=True, reason="event-day: decide_gate.py, cooldown_seconds honoured")
def test_step_6_banter_stays_silent_past_the_cooldown() -> None:
    """gate_log rows verdict='no_go' with finish_reason='stop' (not an
    error) for every window; zero is_ora rows after t_step6."""
    raise NotImplementedError
