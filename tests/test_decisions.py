"""design/07 item 11: the bound is applied AFTER the writer filter — a busy
writer cannot starve the reader. Live against a throwaway Postgres (same
instance as tests/test_store.py):
docker run --rm -e POSTGRES_PASSWORD=ora -e POSTGRES_USER=ora -e POSTGRES_DB=ora \
    -p 55432:5432 postgres:16
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from loop import decisions, gate_log
from loop.store import Store

DATABASE_URL = "postgresql://ora:ora@localhost:55432/ora"


async def _connect() -> Store | None:
    try:
        return await Store.connect(DATABASE_URL)
    except Exception as exc:
        if "asyncpg" in type(exc).__module__ or isinstance(exc, OSError):
            return None
        raise


@pytest.fixture
async def store():
    s = await _connect()
    if s is None:
        pytest.skip(f"no Postgres at {DATABASE_URL} — unverified: throwaway instance not up")
    await s.apply_schema()
    yield s
    await s.execute("TRUNCATE decisions, gate_log, model_calls RESTART IDENTITY")
    await s.close()


async def test_bound_is_applied_after_the_writer_filter(store: Store) -> None:
    """20 `proactive_decide` rows (a busy writer) followed by one `tag` row
    (rare). A `limit=5` query filtered to `writer='tag'` must still return
    that one row — if the bound ran BEFORE the filter (take the last 5
    rows, then keep only `tag`), it would come back empty."""
    now = datetime.now(UTC)
    for i in range(20):
        await decisions.record(
            store,
            workspace="demo",
            writer="proactive_decide",
            verdict="decline",
            platform="signal",
            conversation_id="g1",
            ts=now - timedelta(seconds=20 - i),
        )
    await decisions.record(
        store,
        workspace="demo",
        writer="tag",
        verdict="replied",
        platform="signal",
        conversation_id="g1",
        ts=now - timedelta(hours=1),  # older than every proactive row above
    )

    rows = await decisions.recent_for(store, "demo", ("tag",), limit=5)

    assert len(rows) == 1
    assert rows[0]["writer"] == "tag"


async def test_recent_for_returns_oldest_first(store: Store) -> None:
    now = datetime.now(UTC)
    for i in range(3):
        await decisions.record(
            store,
            workspace="demo",
            writer="participation",
            verdict="spoke",
            platform="signal",
            conversation_id="g1",
            ts=now - timedelta(minutes=3 - i),
        )
    rows = await decisions.recent_for(store, "demo", (), limit=10)
    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps)


async def test_recent_for_is_workspace_scoped(store: Store) -> None:
    await decisions.record(
        store,
        workspace="demo",
        writer="tag",
        verdict="replied",
        platform="signal",
        conversation_id="g1",
    )
    await decisions.record(
        store,
        workspace="other-workspace",
        writer="tag",
        verdict="replied",
        platform="signal",
        conversation_id="g1",
    )
    rows = await decisions.recent_for(store, "demo", (), limit=10)
    assert len(rows) == 1
    assert rows[0]["workspace"] == "demo"


async def test_gate_log_records_and_reads_back(store: Store) -> None:
    call_id = await store.fetchval(
        """INSERT INTO model_calls
           (stage, model, reasoning, prompt_sha, prompt_chars, latency_ms,
            finish_reason, ok, started_at)
           VALUES ('gate','deepseek-v4-flash','none','abc123def456',10,100,'stop',TRUE,$1)
           RETURNING id""",
        datetime.now(UTC),
    )
    await gate_log.record(
        store,
        platform="whatsapp",
        conversation_id="w1",
        verdict="go",
        score=0.81,
        window_rows=3,
        window_end_row_id=1,
        call_id=call_id,
    )
    rows = await gate_log.recent(store, platform="whatsapp", conversation_id="w1")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "go"
