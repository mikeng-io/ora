"""The console log grammar (design/01 §7) — part of the demo, not a debug
dump. Two sinks from one event: a JSON line to `ora.log`, one readable
fixed-grammar line to the console. Third-party loggers are held at WARNING
so nothing else prints.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

STAGES: tuple[str, ...] = (
    "OBSERVE",
    "GATE",
    "TURN",
    "TAG",
    "FOLD",
    "CURATION",
    "PROACTIVE",
    "ACT",
    "DROP",
)

STAGE_WIDTH = 12
ROOM_WIDTH = 11

STAGE_COLOR: dict[str, str] = {
    "OBSERVE": "cyan",
    "GATE": "yellow",
    "TURN": "green",
    "TAG": "magenta",
    "FOLD": "blue",
    "CURATION": "blue",
    "PROACTIVE": "green",
    "ACT": "green",
    "DROP": "grey50",
}

ERROR_COLOR = "red"

_STAGE_ALTERNATION = "|".join(STAGES)
LINE_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2}  (?:" + _STAGE_ALTERNATION + r")\s+\S.*$"
)

_THIRD_PARTY_LOGGERS = ("aiohttp", "asyncpg", "openai", "httpx", "httpcore", "urllib3")


def _pad(value: str, width: int) -> str:
    if len(value) >= width:
        return value + " "
    return value.ljust(width)


def format_line(ts: datetime, stage: str, room: str, what: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; the stage set is closed ({STAGES})")
    return f"{ts:%H:%M:%S}  {_pad(stage, STAGE_WIDTH)}{_pad(room, ROOM_WIDTH)}{what}"


@dataclass
class LogSink:
    json_path: Path
    console: Console = field(default_factory=lambda: Console())
    _dropped_once: set[tuple[str, str]] = field(default_factory=set)

    def event(
        self,
        stage: str,
        room: str,
        what: str,
        *,
        error: bool = False,
        ts: datetime | None = None,
        **fields: object,
    ) -> str | None:
        """Emit one event to both sinks. Returns the console line, or None if
        this was a DROP already emitted once for this room (dedup, design/01 §7)."""
        ts = ts or datetime.now(UTC)

        if stage == "DROP":
            key = (str(fields.get("platform", "")), str(fields.get("conversation_id", room)))
            if key in self._dropped_once:
                return None
            self._dropped_once.add(key)

        line = format_line(ts, stage, room, what)
        color = ERROR_COLOR if error else STAGE_COLOR.get(stage, "white")
        self.console.print(line, style=color)

        record = {"ts": ts.isoformat(), "stage": stage, "room": room, "what": what, **fields}
        with self.json_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

        return line


def configure_third_party_loggers() -> None:
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
