"""`python -m loop_tail`: the console event log tailed live,
with decisions / standing / notes / reminders rendered with ages,
refreshing. The operator's view and the
demo's second screen — nothing else printed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from loop.config import load_config
from loop.logging import LINE_RE, format_line
from loop.render import age
from loop.store import Store


async def _tail_log(path: Path, console: Console, stop: asyncio.Event) -> None:
    """Follow `ora.log`'s JSON lines, printing each in the readable grammar
    as it lands — the same stream `01 §7` defines, tailed rather than
    re-emitted."""
    while not path.exists() and not stop.is_set():
        await asyncio.sleep(0.5)
    if stop.is_set():
        return
    with path.open() as f:
        f.seek(0, 2)  # start at the end — this is a tail, not a replay
        while not stop.is_set():
            line = f.readline()
            if not line:
                await asyncio.sleep(0.3)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = datetime.fromisoformat(record["ts"])
            rendered = format_line(ts, record["stage"], record["room"], record["what"])
            assert LINE_RE.match(rendered)  # the grammar test, live
            console.print(rendered)


async def _fetch(store: Store) -> dict[str, list]:
    return {
        "decisions": await store.fetch("SELECT * FROM decisions ORDER BY ts DESC LIMIT 20"),
        "standing": await store.fetch("SELECT * FROM standing"),
        "notes": await store.fetch(
            "SELECT * FROM note WHERE retired_at IS NULL ORDER BY created_at"
        ),
        "reminders": await store.fetch("SELECT * FROM reminders ORDER BY due_at"),
    }


def _table(title: str, columns: list[str]) -> Table:
    t = Table(title=title, show_lines=False)
    for c in columns:
        t.add_column(c)
    return t


def render_tables(data: dict[str, list], now: datetime) -> list[Table]:
    tables = []

    decisions = _table(
        "decisions", ["age", "workspace", "writer", "verdict", "room", "note", "reminder"]
    )
    for r in data["decisions"]:
        decisions.add_row(
            age(r["ts"], now), r["workspace"], r["writer"], r["verdict"],
            r["conversation_id"], r["note_id"] or "", r["reminder_id"] or "",
        )
    tables.append(decisions)

    standing = _table("standing", ["room", "age", "fold_count", "body"])
    for r in data["standing"]:
        standing.add_row(
            r["conversation_id"], age(r["updated_at"], now), str(r["fold_count"]),
            (r["body"] or "")[:80],
        )
    tables.append(standing)

    notes = _table("notes (open)", ["id", "title", "closing_condition", "age", "room", "strikes"])
    for r in data["notes"]:
        notes.add_row(
            r["id"], r["title"], r["closing_condition"], age(r["created_at"], now),
            r["room_label"], str(r["strikes"]),
        )
    tables.append(notes)

    reminders = _table("reminders", ["id", "note", "state", "due", "age"])
    for r in data["reminders"]:
        reminders.add_row(
            r["id"], r["note_id"] or "", r["state"], str(r["due_at"]), age(r["created_at"], now)
        )
    tables.append(reminders)

    return tables


async def _tail_tables(
    store: Store, console: Console, stop: asyncio.Event, interval: float
) -> None:
    while not stop.is_set():
        data = await _fetch(store)
        for table in render_tables(data, datetime.now(UTC)):
            console.print(table)
        await asyncio.sleep(interval)


async def main() -> None:
    config = load_config()
    console = Console()
    store = await Store.connect(config.env.database_url)
    stop = asyncio.Event()
    log_path = Path(config.env.ora_log)
    try:
        await asyncio.gather(
            _tail_log(log_path, console, stop),
            _tail_tables(store, console, stop, interval=2.0),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop.set()
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
