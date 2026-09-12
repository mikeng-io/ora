"""The tag path against a fake tool-calling model, a fake Toolbox, and a
recording fake store — no network, no real Postgres, no real Ollama/Exa/
Google calls (the patterns are `tests/test_journey.py`'s fake model client
and `tests/test_vision.py`'s recording store).

`loop/tag.py` no longer runs tools before asking the model anything (that
keyword-matched cut could not tell «點樣去中環» from «中環好唔好玩»). It now
hands the model a `Toolbox` via `loop/toolcall.py::run` and lets the model
decide what to call, in as many rounds as it needs. So the fake model here
exposes `complete_with_tools(...) -> ToolCompletion`-shaped objects,
scriptable per round: a round can request tools (`finish_reason=
"tool_calls"`) or answer (`finish_reason="stop"`).

Covers the fail-closed matrix `loop/tag.py`'s docstring asks for: a
grounded `speak` replies and records `tag/replied` with the right
`grounded_on`; a route- and a search-backed answer each get their own
`tool:*` label, now via a real request/run/respond round trip; a tool that
returned nothing is never credited even when the model claims it; a model
timeout / transport error / unparseable body / non-`stop` finish /
non-`speak` verdict each record `'failed'` and never call `deliver`; a
failed `deliver` is still recorded, not dropped; `call_id` reaches the
decisions row on every path (the LAST round's call, per
`ToolLoopResult.call_id`); and a model that keeps requesting tools forever
is bounded by `max_rounds` rather than hanging the turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from loop.act import Delivery
from loop.config import Clocks, Config, Env
from loop.people import PeopleDirectory
from loop.render import NoteEntry, StandingEntry
from loop.tag import handle_tag
from loop.toolcall import MAX_ROUNDS, Toolbox

NOW = datetime(2026, 9, 12, 12, 1, 44, tzinfo=UTC)


def _config() -> Config:
    return Config(env=Env(), clocks=Clocks(), workspaces={"demo": _workspace()}, rooms={})


def _workspace():
    from loop.config import Workspace

    return Workspace(name="demo", home_room="signal")


# --- fakes -----------------------------------------------------------------


def _tool_call(call_id: str, name: str, arguments: str) -> dict:
    """One entry of `ToolCompletion.tool_calls`, and the matching
    `assistant_message` echo — the exact shapes `loop/model.py`'s real
    `complete_with_tools` produces, so the fake round trips the same way
    `loop/toolcall.py::run` expects."""
    return {"id": call_id, "name": name, "arguments": arguments}


def _assistant_message(calls: list[dict]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for c in calls
        ],
    }


@dataclass
class FakeToolCompletion:
    """Mirrors `loop.model.ToolCompletion` — the fake never imports the
    real dataclass so a test never accidentally exercises the real client."""

    content: str = ""
    finish_reason: str = "stop"
    ok: bool = True
    call_id: int | None = 1
    tool_calls: list[dict] = field(default_factory=list)
    assistant_message: dict = field(default_factory=dict)
    error_kind: str | None = None


def _tool_round(
    call_id: int, name: str, arguments: str, *, tool_call_id: str = "c1"
) -> FakeToolCompletion:
    """A round where the model asks for one tool call."""
    calls = [_tool_call(tool_call_id, name, arguments)]
    return FakeToolCompletion(
        content="",
        finish_reason="tool_calls",
        ok=True,
        call_id=call_id,
        tool_calls=calls,
        assistant_message=_assistant_message(calls),
    )


@dataclass
class FakeModelClient:
    """Keyed by stage, one scripted response per round — the
    `tests/test_journey.py` pattern, extended to `complete_with_tools`."""

    responses: dict[str, list[FakeToolCompletion]] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    def script(self, stage: str, response: FakeToolCompletion) -> None:
        self.responses.setdefault(stage, []).append(response)

    async def complete_with_tools(
        self, *, stage: str, messages: list[dict], tools: list[dict], **kwargs: object
    ) -> FakeToolCompletion:
        self.calls.append({"stage": stage, "messages": messages, "tools": tools, **kwargs})
        queue = self.responses.get(stage)
        if not queue:
            raise AssertionError(
                f"no scripted response for stage {stage!r}, round {len(self.calls)}"
            )
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
        FakeToolCompletion(
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
        FakeToolCompletion(
            content='{"verdict":"speak","text":"as we said","grounded_on":"standing"}'
        ),
    )
    standing = StandingEntry(body="we're meeting at Cyberport at 10", updated_at=NOW)

    result, _store, recorder, _deliver = await _run(model, standing=standing)

    assert result.ok
    assert result.grounded_on == "standing"
    assert recorder.rows[0].note_id is None


# --- tool grounding, now via a real request/run/respond round trip -------


async def test_route_backed_answer_records_tool_route() -> None:
    async def fake_route(**_kwargs):
        return {
            "status": "ok",
            "duration_seconds": 1800,
            "distance_meters": 12000,
            "legs": [],
        }

    model = FakeModelClient()
    model.script("tag", _tool_round(101, "route", '{"destination":"Cyberport"}'))
    model.script(
        "tag",
        FakeToolCompletion(
            content='{"verdict":"speak","text":"about 30 minutes",'
            '"grounded_on":"tool:route"}',
            call_id=102,
        ),
    )

    toolbox = Toolbox(route_fn=fake_route, route_api_key="fake-key")
    result, _store, recorder, _deliver = await _run(model, toolbox=toolbox)

    assert result.ok
    assert result.grounded_on == "tool:route"
    assert recorder.rows[0].grounded_on == "tool:route"
    # the last round's call is what gets cited (ORA-17)
    assert recorder.rows[0].call_id == 102
    # the tool result actually reached the model, in the next round
    assert any(
        m.get("role") == "tool" and "1800" in m.get("content", "")
        for m in model.calls[-1]["messages"]
    )


async def test_search_backed_answer_records_tool_search() -> None:
    class FakeSearch:
        async def search(self, *, query: str):
            return {
                "status": "ok",
                "results": [{"url": "https://example.com", "content": "<untrusted>x</untrusted>"}],
            }

    model = FakeModelClient()
    model.script("tag", _tool_round(201, "search", '{"query":"cyberport opening hours"}'))
    model.script(
        "tag",
        FakeToolCompletion(
            content='{"verdict":"speak","text":"here you go","grounded_on":"tool:search"}',
            call_id=202,
        ),
    )

    toolbox = Toolbox(search_provider=FakeSearch())
    result, _store, recorder, _deliver = await _run(model, toolbox=toolbox)

    assert result.ok
    assert result.grounded_on == "tool:search"
    assert recorder.rows[0].grounded_on == "tool:search"


async def test_a_tool_that_returns_nothing_is_not_credited() -> None:
    async def fake_route(**_kwargs):
        return {"status": "no_route", "note": "Google found no route between those places."}

    model = FakeModelClient()
    model.script("tag", _tool_round(301, "route", '{"destination":"Cyberport"}'))
    # the model claims tool:route even though the tool found nothing
    model.script(
        "tag",
        FakeToolCompletion(
            content='{"verdict":"speak","text":"about 30 minutes",'
            '"grounded_on":"tool:route"}',
            call_id=302,
        ),
    )

    toolbox = Toolbox(route_fn=fake_route, route_api_key="fake-key")
    result, _store, recorder, deliver = await _run(model, toolbox=toolbox)

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
    model.script("tag", _tool_round(401, "search", '{"query":"cyberport opening hours"}'))
    model.script(
        "tag",
        FakeToolCompletion(
            content='{"verdict":"speak","text":"here you go","grounded_on":"tool:search"}',
            call_id=402,
        ),
    )

    toolbox = Toolbox(search_provider=FakeSearch())
    result, _store, _recorder, deliver = await _run(model, toolbox=toolbox)

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0


# --- new: the tool-calling mechanics themselves ---------------------------


async def test_a_requested_tool_actually_runs_and_feeds_the_next_round() -> None:
    """The old file could not express this at all: tools used to run
    before the model was ever asked anything. Now the model must ask, the
    toolbox must actually invoke the real (here, fake) function, and the
    result must show up as a `tool`-role message in the following round."""
    invocations: list[dict] = []

    async def fake_route(**kwargs):
        invocations.append(kwargs)
        return {"status": "ok", "duration_seconds": 900, "distance_meters": 3000, "legs": []}

    model = FakeModelClient()
    model.script("tag", _tool_round(501, "route", '{"destination":"Cyberport"}'))
    model.script(
        "tag",
        FakeToolCompletion(
            content='{"verdict":"speak","text":"15 minutes","grounded_on":"tool:route"}',
            call_id=502,
        ),
    )

    toolbox = Toolbox(route_fn=fake_route, route_api_key="fake-key")
    result, _store, _recorder, deliver = await _run(model, toolbox=toolbox)

    assert len(invocations) == 1  # the tool actually ran, exactly once
    assert invocations[0]["destination"] == "Cyberport"
    assert result.ok
    assert result.grounded_on == "tool:route"
    assert len(deliver.calls) == 1
    second_round_messages = model.calls[1]["messages"]
    assert any(
        m.get("role") == "tool" and "900" in m.get("content", "")
        for m in second_round_messages
    )


async def test_max_rounds_exhausted_ends_failed_without_sending() -> None:
    """A model that keeps asking for tools forever must not hang a live
    turn. `toolcall.MAX_ROUNDS` bounds it; the turn ends `failed` and
    nothing is ever sent — the old keyword-matched file had no round loop
    to bound in the first place."""

    async def fake_weather(**_kwargs):
        return {"status": "ok", "summary": "sunny"}

    model = FakeModelClient()
    for i in range(MAX_ROUNDS):
        model.script("tag", _tool_round(600 + i, "weather", '{"when":"now"}', tool_call_id=f"c{i}"))

    toolbox = Toolbox(weather_fn=fake_weather)
    result, _store, recorder, deliver = await _run(model, toolbox=toolbox)

    assert not result.ok
    assert result.verdict == "failed"
    assert result.reason == "max_rounds"
    assert len(deliver.calls) == 0
    assert recorder.rows[0].verdict == "failed"
    assert recorder.rows[0].grounded_on is None
    # exactly MAX_ROUNDS rounds were attempted, no more
    assert len(model.calls) == MAX_ROUNDS


# --- model-side fail-closed paths ----------------------------------------


async def test_a_model_timeout_records_failed_and_never_delivers() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeToolCompletion(
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


async def test_a_transport_error_from_the_model_records_failed() -> None:
    """Distinct from a scripted timeout completion: here the model call
    itself raises, exercising `toolcall.run`'s own `except Exception`
    rather than a completion `loop/model.py` already turned into a
    result."""

    class RaisingModel:
        async def complete_with_tools(self, **_kwargs):
            raise ConnectionError("boom")

    result, _store, recorder, deliver = await _run(RaisingModel())

    assert not result.ok
    assert result.verdict == "failed"
    assert result.reason == "ConnectionError"
    assert result.call_id is None
    assert len(deliver.calls) == 0
    assert recorder.rows[0].call_id is None


async def test_a_non_stop_finish_records_failed() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeToolCompletion(
            content="cut off", finish_reason="length", ok=False, call_id=8, error_kind="length"
        ),
    )

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "length"
    assert len(deliver.calls) == 0


async def test_unparseable_json_records_failed() -> None:
    model = FakeModelClient()
    model.script("tag", FakeToolCompletion(content="not json at all", call_id=9))

    result, _store, recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "unparseable_json"
    assert recorder.rows[0].call_id == 9
    assert len(deliver.calls) == 0


async def test_a_hold_verdict_on_the_tag_path_records_failed() -> None:
    model = FakeModelClient()
    model.script("tag", FakeToolCompletion(content='{"verdict":"hold"}', call_id=10))

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "verdict:hold"
    assert len(deliver.calls) == 0


async def test_empty_text_records_failed() -> None:
    model = FakeModelClient()
    model.script(
        "tag", FakeToolCompletion(content='{"verdict":"speak","text":"","grounded_on":"standing"}')
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
        FakeToolCompletion(
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
    model.script("tag", FakeToolCompletion(content='{"verdict":"speak","text":"hello"}'))

    result, _store, _recorder, deliver = await _run(model)

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0


async def test_a_note_id_not_in_the_notes_block_is_ungrounded() -> None:
    model = FakeModelClient()
    model.script(
        "tag",
        FakeToolCompletion(content='{"verdict":"speak","text":"hello","grounded_on":"note:N9"}'),
    )

    result, _store, _recorder, deliver = await _run(model)  # no notes given

    assert not result.ok
    assert result.reason == "ungrounded"
    assert len(deliver.calls) == 0
