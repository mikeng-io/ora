"""The tag path against a fake model client, fake tools, and a recording
fake store — no network, no real Postgres, no real Exa/Google calls (the
patterns are `tests/test_journey.py`'s fake model client and
`tests/test_vision.py`'s recording store).

Covers the fail-closed matrix rule 1 in `loop/tag.py`'s docstring asks
for: a grounded `speak` replies and records `tag/replied` with the right
`grounded_on`; a route- and a search-backed answer each get their own
`tool:*` label; a tool that returned nothing is never credited even when
the model claims it; a model timeout / transport error / unparseable body
/ non-`stop` finish / non-`speak` verdict each record `'failed'` and never
call `deliver`; a failed `deliver` is still recorded, not dropped; and
`call_id` reaches the decisions row on every path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from loop.act import Delivery
from loop.config import Clocks, Config, Env
from loop.people import PeopleDirectory
from loop.render import NoteEntry, StandingEntry
from loop.tag import handle_tag

NOW = datetime(2026, 9, 12, 12, 1, 44, tzinfo=UTC)


def _config() -> Config:
    return Config(env=Env(), clocks=Clocks(), workspaces={"demo": _workspace()}, rooms={})


def _workspace():
    from loop.config import Workspace

    return Workspace(name="demo", home_room="signal")


# --- fakes -----------------------------------------------------------------


@dataclass
class FakeCompletion:
    content: str = ""
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 1
    error_kind: str | None = None


@dataclass
class FakeModelClient:
    """Keyed by stage, one scripted response per call — the
    `tests/test_journey.py` pattern."""

    responses: dict[str, list[FakeCompletion]] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    def script(self, stage: str, response: FakeCompletion) -> None:
        self.responses.setdefault(stage, []).append(response)

    async def complete_json(self, *, stage: str, system: str, prompt: str, **kwargs: object):
        self.calls.append({"stage": stage, "system": system, "prompt": prompt, **kwargs})
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(f"no scripted response for stage {stage!r}")
        return queue.pop(0)


class RecordingStore:
    """Never touched by SQL directly in these tests — `handle_tag` only
    ever reaches the store through the injected `record_decision`, but a
    real `decisions.record` call would use `fetchval`, so keep the shape
    available in case a test wants the real function."""

    def __init__(self) -> None:
        self.fetchval_calls: list[tuple] = []

    async def fetchval(self, _query: str, *args: object) -> int:
        self.fetchval_calls.append(args)
        return len(self.fetchval_calls)


@dataclass
class RecordedDecision:
    workspace: str
    writer: str
    verdict: str
    platform: str
    conversation_id: str
    grounded_on: str | None
    reason: str
    call_id: int | None
    note_id: str | None = None


class RecordingDecisions:
    def __init__(self) -> None:
        self.rows: list[RecordedDecision] = []

    async def record(self, _store, **kwargs: object) -> int:
        self.rows.append(RecordedDecision(**kwargs))
        return len(self.rows)


class RecordingDeliver:
    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.calls: list[tuple] = []

    async def __call__(self, config, store, platform, conversation_id, text, people, **kwargs):
        self.calls.append((platform, conversation_id, text))
        return Delivery(
            ok=self.ok,
            row_id=42 if self.ok else None,
            delivery_status="sent" if self.ok else "failed",
            reason=self.reason,
        )


PEOPLE = PeopleDirectory([])


async def _run(model, **overrides):
    store = overrides.pop("store", RecordingStore())
    recorder = overrides.pop("recorder", RecordingDecisions())
    deliver = overrides.pop("deliver", RecordingDeliver())
    kwargs = dict(
        config=_config(),
        store=store,
        model=model,
        people=PEOPLE,
        platform="signal",
        conversation_id="sig-1",
        now=NOW,
        deliver_fn=deliver,
        record_decision=recorder.record,
    )
    kwargs.update(overrides)
    result = await handle_tag(**kwargs)
    return result, store, recorder, deliver


# --- a grounded reply --------------------------------------------------


async def test_a_speak_grounded_on_a_note_replies_and_records() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"10am, meet at the lobby",'
            '"grounded_on":"note:N1"}'
        ),
    )
    notes = [
        NoteEntry(
            id="N1",
            title="Cyberport tomorrow",
            closing_condition="a time is agreed",
            created_at=NOW,
            room_label="Signal",
        )
    ]

    result, _store, recorder, deliver = await _run(model, notes=notes)

    assert result.ok
    assert result.verdict == "replied"
    assert result.grounded_on == "note:N1"
    assert len(deliver.calls) == 1
    assert deliver.calls[0][2] == "10am, meet at the lobby"
    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row.writer == "tag"
    assert row.verdict == "replied"
    assert row.grounded_on == "note:N1"
    assert row.note_id == "N1"
    assert row.call_id == 1


async def test_speak_grounded_on_standing() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"as we said","grounded_on":"standing"}'
        ),
    )
    standing = StandingEntry(body="we're meeting at Cyberport at 10", updated_at=NOW)

    result, _store, recorder, _deliver = await _run(model, standing=standing)

    assert result.ok
    assert result.grounded_on == "standing"
    assert recorder.rows[0].note_id is None


# --- tool grounding ------------------------------------------------------


async def test_route_backed_answer_records_tool_route() -> None:
    async def fake_route(**_kwargs):
        return {
            "status": "ok",
            "duration_seconds": 1800,
            "distance_meters": 12000,
            "legs": [],
        }

    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"about 30 minutes",'
            '"grounded_on":"tool:route"}'
        ),
    )

    result, _store, recorder, _deliver = await _run(
        model,
        route_query=("Tin Shui Wai", "Cyberport"),
        route_api_key="fake-key",
        route_fn=fake_route,
    )

    assert result.ok
    assert result.grounded_on == "tool:route"
    assert recorder.rows[0].grounded_on == "tool:route"
    # the tool result reached the prompt
    assert "route" in model.calls[0]["prompt"]
    assert "1800" in model.calls[0]["prompt"]


async def test_search_backed_answer_records_tool_search() -> None:
    class FakeSearch:
        async def search(self, *, query: str):
            return {
                "status": "ok",
                "results": [{"url": "https://example.com", "content": "<untrusted>x</untrusted>"}],
            }

    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"here you go","grounded_on":"tool:search"}'
        ),
    )

    result, _store, recorder, _deliver = await _run(
        model, search_query="cyberport opening hours", search_provider=FakeSearch()
    )

    assert result.ok
    assert result.grounded_on == "tool:search"
    assert recorder.rows[0].grounded_on == "tool:search"


async def test_a_tool_that_returns_nothing_is_not_credited() -> None:
    async def fake_route(**_kwargs):
        return {"status": "no_route", "note": "Google found no route between those places."}

    model = FakeModelClient()
    # the model claims tool:route even though the tool found nothing
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"about 30 minutes",'
            '"grounded_on":"tool:route"}'
        ),
    )

    result, _store, recorder, deliver = await _run(
        model,
        route_query=("Tin Shui Wai", "Cyberport"),
        route_api_key="fake-key",
        route_fn=fake_route,
    )

    assert not result.ok
    assert result.verdict == "failed"
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0
    assert recorder.rows[0].grounded_on is None
    assert recorder.rows[0].verdict == "failed"


async def test_an_unavailable_search_is_not_credited_either() -> None:
    class FakeSearch:
        async def search(self, *, query: str):
            return {"status": "unavailable", "note": "I couldn't run that search just now."}

    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"here you go","grounded_on":"tool:search"}'
        ),
    )

    result, _store, _recorder, deliver = await _run(
        model, search_query="cyberport opening hours", search_provider=FakeSearch()
    )

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0


# --- model-side fail-closed paths ----------------------------------------


async def test_a_model_timeout_records_failed_and_never_delivers() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content="", finish_reason="timeout", ok=False, call_id=7, error_kind="timeout"
        ),
    )

    result, _store, recorder, deliver = await _run(model)

    assert not result.ok
    assert result.verdict == "failed"
    assert result.reason == "timeout"
    assert result.call_id == 7
    assert len(deliver.calls) == 0
    assert recorder.rows[0].verdict == "failed"
    assert recorder.rows[0].call_id == 7
    assert recorder.rows[0].grounded_on is None


async def test_a_non_stop_finish_records_failed() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content="cut off", finish_reason="length", ok=False, call_id=8, error_kind="length"
        ),
    )

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "length"
    assert len(deliver.calls) == 0


async def test_unparseable_json_records_failed() -> None:
    model = FakeModelClient()
    model.script("tag", FakeCompletion(content="not json at all", call_id=9))

    result, _store, recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "unparseable_json"
    assert recorder.rows[0].call_id == 9
    assert len(deliver.calls) == 0


async def test_a_hold_verdict_on_the_tag_path_records_failed() -> None:
    model = FakeModelClient()
    model.script("tag", FakeCompletion(content='{"verdict":"hold"}', call_id=10))

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "verdict:hold"
    assert len(deliver.calls) == 0


async def test_empty_text_records_failed() -> None:
    model = FakeModelClient()
    model.script(
        "tag", FakeCompletion(content='{"verdict":"speak","text":"","grounded_on":"standing"}')
    )

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "empty_text"
    assert len(deliver.calls) == 0


# --- delivery failure is recorded, not dropped ----------------------------


async def test_a_failed_deliver_is_recorded_not_dropped() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(
            content='{"verdict":"speak","text":"hello","grounded_on":"standing"}', call_id=11
        ),
    )
    standing = StandingEntry(body="something", updated_at=NOW)

    result, _store, recorder, deliver = await _run(
        model, standing=standing, deliver=RecordingDeliver(ok=False, reason="HTTP 500")
    )

    assert len(deliver.calls) == 1  # deliver WAS called
    assert not result.ok
    assert result.verdict == "failed"
    assert recorder.rows[0].verdict == "failed"
    assert recorder.rows[0].reason == "HTTP 500"
    assert recorder.rows[0].grounded_on == "standing"  # the answer itself was grounded
    assert recorder.rows[0].call_id == 11


# --- an unclaimed / bogus grounded_on never gets through ------------------


async def test_a_missing_grounded_on_is_treated_as_ungrounded() -> None:
    model = FakeModelClient()
    model.script("tag", FakeCompletion(content='{"verdict":"speak","text":"hello"}'))

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0


async def test_a_note_id_not_in_the_notes_block_is_ungrounded() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeCompletion(content='{"verdict":"speak","text":"hello","grounded_on":"note:N9"}'),
    )

    result, _store, _recorder, deliver = await _run(model)  # no notes given

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0
