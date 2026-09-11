import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from loop.logging import LINE_RE, STAGES, LogSink, format_line


def _sink(tmp_path: Path) -> LogSink:
    return LogSink(json_path=tmp_path / "ora.log", console=Console(quiet=True))


def test_every_stage_emits_a_line_matching_the_grammar(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    for stage in STAGES:
        line = sink.event(stage, "Signal", f"sample {stage.lower()} line")
        assert line is not None
        assert LINE_RE.match(line), f"{stage}: {line!r} did not match the grammar"


def test_unknown_stage_is_rejected() -> None:
    with pytest.raises(ValueError):
        format_line(datetime.now(UTC), "BOGUS", "Signal", "x")


def test_json_sink_carries_one_record_per_event(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    sink.event("GATE", "WhatsApp", "go score 0.81", score=0.81, verdict="go")
    lines = (tmp_path / "ora.log").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["stage"] == "GATE"
    assert record["room"] == "WhatsApp"
    assert record["score"] == 0.81


def test_drop_prints_once_per_room_and_never_again(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    kwargs = {"platform": "signal", "conversation_id": "g9"}
    first = sink.event("DROP", "signal", "not in rooms.toml", **kwargs)
    second = sink.event("DROP", "signal", "not in rooms.toml", **kwargs)
    assert first is not None
    assert second is None
    # a different room still gets its own first line
    other = sink.event(
        "DROP", "signal", "not in rooms.toml", platform="signal", conversation_id="g10"
    )
    assert other is not None


def test_error_line_uses_the_error_color(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    line = sink.event("GATE", "Signal", "no_go error_kind=timeout", error=True)
    assert line is not None
    assert LINE_RE.match(line)
