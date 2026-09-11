"""Live against a throwaway Postgres (design/07 item 3):
docker run --rm -e POSTGRES_PASSWORD=ora -e POSTGRES_USER=ora -e POSTGRES_DB=ora \
    -p 55432:5432 postgres:16

Skips cleanly if that instance is not reachable — never fakes a pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from loop.store import Store

DATABASE_URL = "postgresql://ora:ora@localhost:55432/ora"


async def _connect() -> Store | None:
    try:
        return await Store.connect(DATABASE_URL)
    except OSError:
        return None
    except Exception as exc:  # asyncpg raises its own connection errors
        if "asyncpg" in type(exc).__module__:
            return None
        raise


@pytest.fixture
async def store():
    s = await _connect()
    if s is None:
        pytest.skip(f"no Postgres at {DATABASE_URL} — unverified: throwaway instance not up")
    await s.apply_schema()
    yield s
    await s.execute(
        "TRUNCATE messages, standing, note, reminders, decisions, gate_log, model_calls"
    )
    await s.close()


async def test_schema_apply_is_idempotent(store: Store) -> None:
    await store.apply_schema()
    await store.apply_schema()


async def test_insert_and_read_back_one_row_per_table(store: Store) -> None:
    now = datetime.now(UTC)

    call_id = await store.fetchval(
        """INSERT INTO model_calls
           (stage, platform, conversation_id, model, reasoning, prompt_sha,
            prompt_chars, latency_ms, finish_reason, ok, output_chars, started_at)
           VALUES ('gate','signal','g1','deepseek-v4-flash','none','abc123def456',
                   100, 400, 'stop', TRUE, 40, $1)
           RETURNING id""",
        now,
    )
    assert call_id is not None

    msg_id = await store.fetchval(
        """INSERT INTO messages
           (platform, conversation_id, workspace, sender_id, is_ora, ts, body, body_len)
           VALUES ('signal','g1','demo','+85212345678', FALSE, $1, 'hello', 5)
           RETURNING id""",
        now,
    )
    assert msg_id is not None

    await store.execute(
        """INSERT INTO standing (platform, conversation_id, body, body_len, updated_at)
           VALUES ('signal','g1','nothing yet', 11, $1)""",
        now,
    )
    row = await store.fetchrow(
        "SELECT body FROM standing WHERE platform='signal' AND conversation_id='g1'"
    )
    assert row["body"] == "nothing yet"

    await store.execute(
        """INSERT INTO note (id, workspace, room_label, title, closing_condition, created_at)
           VALUES ('N1','demo','Signal','Cyberport tomorrow','a time is agreed', $1)""",
        now,
    )
    note = await store.fetchrow("SELECT title FROM note WHERE id='N1'")
    assert note["title"] == "Cyberport tomorrow"

    await store.execute(
        """INSERT INTO reminders
           (id, workspace, note_id, due_at, state, intent, intent_len, created_at)
           VALUES ('R1','demo','N1', $1, 'pending', 'raise the dinner note', 22, $1)""",
        now,
    )
    reminder = await store.fetchrow("SELECT state FROM reminders WHERE id='R1'")
    assert reminder["state"] == "pending"

    dec_id = await store.fetchval(
        """INSERT INTO decisions
           (workspace, writer, verdict, platform, conversation_id, note_id, call_id, ts)
           VALUES ('demo','participation','spoke','whatsapp','w1','N1', $1, $2)
           RETURNING id""",
        call_id,
        now,
    )
    assert dec_id is not None

    gate_id = await store.fetchval(
        """INSERT INTO gate_log
           (platform, conversation_id, verdict, score, window_rows, window_end_row_id, call_id, ts)
           VALUES ('whatsapp','w1','go', 0.81, 3, $1, $2, $3)
           RETURNING id""",
        msg_id,
        call_id,
        now,
    )
    assert gate_id is not None
