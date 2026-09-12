"""Orient: fold / propose_notes / curate, against a fake model client keyed
by stage (`tests/test_journey.py`'s pattern) and a recording fake store
(`tests/test_vision.py`'s pattern) — no network, no real Postgres.

Covers: a fold rewrites standing (bumps `fold_count`, sets `body_len` and
`last_call_id`); the watermark advances to the last folded row and a
second fold reads only newer rows; a failed fold leaves the previous
standing untouched (the brake, proved by scripting `ok=False`, unparseable
JSON, and a raised exception — all three change nothing); note extraction
ignores `is_ora` rows (the brake, proved by asserting the marker text never
reaches the model at all); an over-long standing body is capped at 1200;
curation closes with a citation, a close without one is refused, and so is
one citing a row outside that note's own evidence; `expired` and `moot`
paths; every failure mode changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from loop import orient
from loop.orient import (
    MessageRow,
    NoteForCuration,
    StandingState,
)
from loop.render import NoteEntry

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


def _row(id_: int, body: str, *, is_ora: bool = False, sender: str = "Mike", ts=None) -> MessageRow:
    return MessageRow(id=id_, sender_label=sender, body=body, ts=ts or NOW, is_ora=is_ora)


# --- fakes -------------------------------------------------------------


@dataclass
class FakeCompletion:
    content: str
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 1
    error_kind: str | None = None


@dataclass
class FakeModelClient:
    """Keyed by stage, same shape as `tests/test_journey.py::FakeModelClient`,
    extended with `call_id`/`error_kind` to match `ModelCompletion`'s real
    shape (orient.py reads both off every completion)."""

    responses: dict[str, list[FakeCompletion]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    raises: Exception | None = None

    def script(self, stage: str, completion: FakeCompletion) -> None:
        self.responses.setdefault(stage, []).append(completion)

    async def complete_json(
        self, *, stage: str, system: str, prompt: str, **kwargs: Any
    ) -> FakeCompletion:
        self.calls.append({"stage": stage, "system": system, "prompt": prompt, **kwargs})
        if self.raises is not None:
            raise self.raises
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r} — script it first")
        return queue.pop(0)


@dataclass
class RecordingStore:
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"


# --- fold: the happy path ------------------------------------------------


async def test_fold_rewrites_standing_bumps_fold_count_sets_body_len_and_call_id() -> None:
    client = FakeModelClient()
    client.script(
        "fold",
        FakeCompletion(content='{"standing": "new position", "notes": []}', call_id=99),
    )
    store = RecordingStore()
    prior = StandingState(
        body="old position", body_len=13, fold_count=2, last_folded_row_id=10,
        last_call_id=5, updated_at=NOW - timedelta(minutes=5),
    )
    rows = [_row(11, "anyone free tomorrow?"), _row(12, "yeah I am")]

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=rows, prior=prior, now=NOW,
    )

    assert result.changed is True
    assert result.body == "new position"
    assert result.body_len == len("new position")
    assert result.fold_count == 3
    assert result.last_folded_row_id == 12
    assert result.last_call_id == 99
    assert result.call_id == 99
    assert result.error_kind is None
    assert len(store.executed) == 1
    query, args = store.executed[0]
    assert "INSERT INTO standing" in query
    assert args == ("signal", "room1", "new position", len("new position"), 3, 12, 99, NOW)


# --- fold: the watermark rule (both directions) --------------------------


async def test_fold_watermark_advances_and_a_second_fold_reads_only_newer_rows() -> None:
    client = FakeModelClient()
    client.script("fold", FakeCompletion(content='{"standing": "position A"}', call_id=1))
    client.script("fold", FakeCompletion(content='{"standing": "position B"}', call_id=2))
    store = RecordingStore()
    prior = StandingState()

    first = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(11, "one"), _row(12, "two")], prior=prior, now=NOW,
    )
    assert first.last_folded_row_id == 12
    assert first.fold_count == 1

    prior2 = StandingState(
        body=first.body, body_len=first.body_len, fold_count=first.fold_count,
        last_folded_row_id=first.last_folded_row_id, last_call_id=first.last_call_id,
        updated_at=first.updated_at,
    )
    second = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(13, "three"), _row(14, "four")], prior=prior2, now=NOW,
    )

    assert second.last_folded_row_id == 14
    assert second.fold_count == 2
    # the second call's prompt carries only the newer rows
    second_prompt = client.calls[1]["prompt"]
    assert "three" in second_prompt and "four" in second_prompt
    assert "one" not in second_prompt and "two" not in second_prompt


async def test_fold_with_no_new_rows_does_not_advance_the_watermark_or_call_the_model() -> None:
    client = FakeModelClient()
    store = RecordingStore()
    prior = StandingState(
        body="steady", body_len=6, fold_count=4, last_folded_row_id=40,
        last_call_id=7, updated_at=NOW,
    )

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[], prior=prior, now=NOW,
    )

    assert result.changed is False
    assert result.last_folded_row_id == 40
    assert result.fold_count == 4
    assert result.body == "steady"
    assert client.calls == []
    assert store.executed == []


# --- fold: the brake — a failed fold leaves the previous standing untouched --


async def test_failed_fold_on_a_non_ok_completion_leaves_standing_untouched() -> None:
    client = FakeModelClient()
    client.script("fold", FakeCompletion(content="", ok=False, call_id=3, error_kind="timeout"))
    store = RecordingStore()
    prior = StandingState(
        body="kept as is", body_len=10, fold_count=1, last_folded_row_id=5,
        last_call_id=2, updated_at=NOW,
    )

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(6, "something new")], prior=prior, now=NOW,
    )

    assert result.changed is False
    assert result.body == "kept as is"
    assert result.fold_count == 1
    assert result.last_folded_row_id == 5
    assert result.last_call_id == 2
    assert result.error_kind == "timeout"
    assert store.executed == []


async def test_failed_fold_on_unparseable_json_leaves_standing_untouched() -> None:
    client = FakeModelClient()
    client.script("fold", FakeCompletion(content="not json at all"))
    store = RecordingStore()
    prior = StandingState(body="kept as is", body_len=10, fold_count=1, last_folded_row_id=5)

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(6, "x")], prior=prior, now=NOW,
    )

    assert result.changed is False
    assert result.body == "kept as is"
    assert result.error_kind == "unparseable_json"
    assert store.executed == []


async def test_failed_fold_on_a_raised_exception_leaves_standing_untouched() -> None:
    client = FakeModelClient(raises=ConnectionError("boom"))
    store = RecordingStore()
    prior = StandingState(body="kept as is", body_len=10, fold_count=1, last_folded_row_id=5)

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(6, "x")], prior=prior, now=NOW,
    )

    assert result.changed is False
    assert result.body == "kept as is"
    assert result.error_kind == "ConnectionError"
    assert store.executed == []


async def test_failed_fold_on_a_blank_standing_string_leaves_standing_untouched() -> None:
    client = FakeModelClient()
    client.script("fold", FakeCompletion(content='{"standing": "   "}'))
    store = RecordingStore()
    prior = StandingState(body="kept as is", body_len=10, fold_count=1, last_folded_row_id=5)

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(6, "x")], prior=prior, now=NOW,
    )

    assert result.changed is False
    assert result.body == "kept as is"
    assert result.error_kind == "unparseable_json"
    assert store.executed == []


# --- fold: the 1200-char cap ----------------------------------------------


async def test_over_long_standing_body_is_capped_at_1200_chars() -> None:
    client = FakeModelClient()
    long_body = "x" * 5000
    client.script("fold", FakeCompletion(content=f'{{"standing": "{long_body}"}}'))
    store = RecordingStore()
    prior = StandingState()

    result = await orient.fold(
        client, store, platform="signal", conversation_id="room1",
        rows=[_row(1, "hi")], prior=prior, now=NOW,
    )

    assert result.changed is True
    assert len(result.body) == 1200
    assert result.body_len == 1200
    _, args = store.executed[0]
    assert args[2] == result.body
    assert args[3] == 1200


# --- propose_notes: the brake — human rows only ---------------------------


async def test_note_extraction_ignores_is_ora_rows() -> None:
    marker = "ORA SAID THIS AND IT MUST NOT SEED A NOTE"
    client = FakeModelClient()
    client.script(
        "notes",
        FakeCompletion(
            content=(
                '{"verdicts": [{"id": 2, "ahead": true, "committed": true, '
                '"open": ["which time"]}], '
                '"candidates": [{"title": "Saturday plan", '
                '"closing_condition": "a time is agreed", "message_ids": [2]}]}'
            )
        ),
    )
    store = RecordingStore()
    rows = [
        _row(1, marker, is_ora=True, sender="Ora"),
        _row(2, "anyone free Saturday? I'm in", sender="Mike"),
    ]

    result = await orient.propose_notes(
        client, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )

    assert len(result.created) == 1
    assert result.created[0].title == "Saturday plan"
    # the brake itself: the is_ora row's text never reaches the model
    sent_prompt = client.calls[0]["prompt"]
    assert marker not in sent_prompt
    assert "anyone free Saturday" in sent_prompt


async def test_note_extraction_makes_no_model_call_when_every_row_is_ora() -> None:
    client = FakeModelClient()
    store = RecordingStore()
    rows = [_row(1, "delivered message", is_ora=True, sender="Ora")]

    result = await orient.propose_notes(
        client, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )

    assert result.created == []
    assert client.calls == []
    assert store.executed == []


async def test_note_candidate_citing_a_message_id_outside_the_batch_is_discarded() -> None:
    client = FakeModelClient()
    client.script(
        "notes",
        FakeCompletion(
            content=(
                '{"verdicts": [], "candidates": [{"title": "x", '
                '"closing_condition": "y", "message_ids": [999]}]}'
            )
        ),
    )
    store = RecordingStore()
    rows = [_row(2, "anyone free Saturday?")]

    result = await orient.propose_notes(
        client, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )

    assert result.created == []
    assert store.executed == []


async def test_note_candidate_is_persisted_with_a_workspace_and_room_label_and_call_id() -> None:
    client = FakeModelClient()
    client.script(
        "notes",
        FakeCompletion(
            content=(
                '{"verdicts": [], "candidates": [{"title": "Dinner plan", '
                '"closing_condition": "a place is agreed", "anchor_date": 20260930, '
                '"message_ids": [2]}]}'
            ),
            call_id=42,
        ),
    )
    store = RecordingStore()
    rows = [_row(2, "where should we eat?")]

    result = await orient.propose_notes(
        client, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main Room", rows=rows, open_notes=[], now=NOW,
    )

    assert len(result.created) == 1
    created = result.created[0]
    assert len(created.id) == 8  # 8-hex, per schema
    assert created.anchor_at == datetime(2026, 9, 30, tzinfo=UTC)
    assert created.created_call_id == 42
    query, args = store.executed[0]
    assert "INSERT INTO note" in query
    assert args[1] == "demo"
    assert args[2] == "Main Room"
    assert args[8] == 42


# --- propose_notes: failure modes change nothing --------------------------


async def test_propose_notes_failure_modes_create_nothing() -> None:
    store = RecordingStore()
    rows = [_row(2, "anyone free Saturday?")]

    not_ok = FakeModelClient()
    not_ok.script("notes", FakeCompletion(content="", ok=False, error_kind="timeout"))
    result = await orient.propose_notes(
        not_ok, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )
    assert result.created == []
    assert result.error_kind == "timeout"

    bad_json = FakeModelClient()
    bad_json.script("notes", FakeCompletion(content="not json"))
    result = await orient.propose_notes(
        bad_json, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )
    assert result.created == []
    assert result.error_kind == "unparseable_json"

    raising = FakeModelClient(raises=TimeoutError("slow"))
    result = await orient.propose_notes(
        raising, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=rows, open_notes=[], now=NOW,
    )
    assert result.created == []
    assert result.error_kind == "TimeoutError"

    assert store.executed == []


# --- curate: closing with a citation --------------------------------------


async def test_curation_closes_with_a_citation() -> None:
    client = FakeModelClient()
    client.script(
        "curation",
        FakeCompletion(
            content=(
                '{"verdicts": [{"note_id": "N1", "verdict": "closed", '
                '"why": "Ada booked it", "cited_message_id": 50}]}'
            )
        ),
    )
    store = RecordingStore()
    note = NoteForCuration(
        id="N1", title="dinner spot", closing_condition="a place is agreed",
        anchor_at=None, evidence=[_row(50, "let's do Yardbird")],
    )

    result = await orient.curate(client, store, notes=[note], now=NOW)

    assert result.retired == [orient.RetiredNote(id="N1", reason="closed", cited_row_id=50)]
    query, args = store.executed[0]
    assert "UPDATE note" in query
    assert args == (NOW, "closed", 50, "N1")


async def test_curation_close_without_a_citation_is_refused() -> None:
    client = FakeModelClient()
    client.script(
        "curation",
        FakeCompletion(
            content='{"verdicts": [{"note_id": "N1", "verdict": "closed", "why": "settled"}]}'
        ),
    )
    store = RecordingStore()
    note = NoteForCuration(
        id="N1", title="dinner spot", closing_condition="a place is agreed",
        anchor_at=None, evidence=[_row(50, "let's do Yardbird")],
    )

    result = await orient.curate(client, store, notes=[note], now=NOW)

    assert result.retired == []
    assert store.executed == []


async def test_curation_close_citing_a_row_outside_this_notes_evidence_is_refused() -> None:
    client = FakeModelClient()
    client.script(
        "curation",
        FakeCompletion(
            content=(
                '{"verdicts": [{"note_id": "N1", "verdict": "closed", '
                '"why": "settled", "cited_message_id": 999}]}'
            )
        ),
    )
    store = RecordingStore()
    note = NoteForCuration(
        id="N1", title="dinner spot", closing_condition="a place is agreed",
        anchor_at=None, evidence=[_row(50, "let's do Yardbird")],
    )

    result = await orient.curate(client, store, notes=[note], now=NOW)

    assert result.retired == []
    assert store.executed == []


# --- curate: expired / moot / open ----------------------------------------


async def test_curation_expired_and_moot_paths_need_no_citation() -> None:
    client = FakeModelClient()
    client.script(
        "curation",
        FakeCompletion(
            content=(
                '{"verdicts": ['
                '{"note_id": "N1", "verdict": "expired", "why": "the 9th came and went"}, '
                '{"note_id": "N2", "verdict": "moot", "why": "trip was cancelled"}'
                ']}'
            )
        ),
    )
    store = RecordingStore()
    notes = [
        NoteForCuration(id="N1", title="a", closing_condition="c1", anchor_at=NOW, evidence=[]),
        NoteForCuration(id="N2", title="b", closing_condition="c2", anchor_at=None, evidence=[]),
    ]

    result = await orient.curate(client, store, notes=notes, now=NOW)

    assert {r.id: r.reason for r in result.retired} == {"N1": "expired", "N2": "moot"}
    assert all(r.cited_row_id is None for r in result.retired)
    assert len(store.executed) == 2


async def test_curation_open_verdict_retires_nothing() -> None:
    client = FakeModelClient()
    client.script(
        "curation",
        FakeCompletion(
            content='{"verdicts": [{"note_id": "N1", "verdict": "open", "why": "still open"}]}'
        ),
    )
    store = RecordingStore()
    note = NoteForCuration(id="N1", title="a", closing_condition="c", anchor_at=None, evidence=[])

    result = await orient.curate(client, store, notes=[note], now=NOW)

    assert result.retired == []
    assert store.executed == []


async def test_curate_with_no_open_notes_makes_no_model_call() -> None:
    client = FakeModelClient()
    store = RecordingStore()

    result = await orient.curate(client, store, notes=[], now=NOW)

    assert result.retired == []
    assert client.calls == []
    assert store.executed == []


# --- curate: failure modes change nothing ---------------------------------


async def test_curation_failure_modes_retire_nothing() -> None:
    note = NoteForCuration(
        id="N1", title="a", closing_condition="c", anchor_at=None, evidence=[_row(1, "x")]
    )
    store = RecordingStore()

    not_ok = FakeModelClient()
    not_ok.script("curation", FakeCompletion(content="", ok=False, error_kind="timeout"))
    result = await orient.curate(not_ok, store, notes=[note], now=NOW)
    assert result.retired == []
    assert result.error_kind == "timeout"

    bad_json = FakeModelClient()
    bad_json.script("curation", FakeCompletion(content="not json"))
    result = await orient.curate(bad_json, store, notes=[note], now=NOW)
    assert result.retired == []
    assert result.error_kind == "unparseable_json"

    raising = FakeModelClient(raises=RuntimeError("kaboom"))
    result = await orient.curate(raising, store, notes=[note], now=NOW)
    assert result.retired == []
    assert result.error_kind == "RuntimeError"

    assert store.executed == []


# --- a sanity check that render_notes (not a hand-rolled index) is used ---


async def test_propose_notes_shows_the_open_notes_index_via_render_notes() -> None:
    client = FakeModelClient()
    client.script("notes", FakeCompletion(content='{"verdicts": [], "candidates": []}'))
    store = RecordingStore()
    open_notes = [
        NoteEntry(
            id="N1", title="Existing plan", closing_condition="a time is agreed",
            created_at=NOW - timedelta(hours=1), room_label="Main",
        )
    ]

    await orient.propose_notes(
        client, store, workspace="demo", platform="signal", conversation_id="room1",
        room_label="Main", rows=[_row(2, "hello")], open_notes=open_notes, now=NOW,
    )

    prompt = client.calls[0]["prompt"]
    assert "Existing plan" in prompt
    assert "N1" in prompt


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
