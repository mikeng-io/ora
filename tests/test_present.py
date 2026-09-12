"""Coverage for loop/present.py's broadcaster only — no browser, no Live2D.
The property under test throughout is the fail-closed one the module
docstring promises: a real send into Signal/WhatsApp must never be put at
risk by a stage view that is absent, slow, or unbindable."""

import asyncio
import json

import pytest

from loop.present import _QUEUE_MAXSIZE, Presenter


async def test_subscriber_receives_a_published_line() -> None:
    presenter = Presenter()
    queue = presenter.subscribe()

    await presenter.speak("hello room", room="fam", platform="signal")

    raw = queue.get_nowait()
    assert raw.startswith("data: ")
    payload = json.loads(raw[len("data: ") :].strip())
    assert payload["text"] == "hello room"
    assert payload["room"] == "fam"
    assert payload["platform"] == "signal"
    assert isinstance(payload["ts"], float)


async def test_publish_with_no_subscribers_does_not_raise() -> None:
    presenter = Presenter()
    # No .subscribe() call at all — this must be a silent no-op.
    await presenter.speak("nobody is watching", room="fam", platform="whatsapp")
    assert presenter.subscriber_count == 0


async def test_multiple_subscribers_all_receive_the_line() -> None:
    presenter = Presenter()
    q1 = presenter.subscribe()
    q2 = presenter.subscribe()

    await presenter.speak("broadcast", room="fam", platform="signal")

    assert json.loads(q1.get_nowait()[len("data: ") :])["text"] == "broadcast"
    assert json.loads(q2.get_nowait()[len("data: ") :])["text"] == "broadcast"


async def test_slow_subscriber_does_not_block_the_publisher() -> None:
    presenter = Presenter()
    slow = presenter.subscribe()

    # Fill the slow subscriber's queue to capacity without ever draining it —
    # this simulates a browser tab that stopped reading.
    for i in range(_QUEUE_MAXSIZE):
        slow.put_nowait(f"stale-{i}")

    # Publishing past capacity must return promptly (no blocking put) and
    # must not raise, even though this exact subscriber is now full.
    await asyncio.wait_for(
        presenter.speak("still going", room="fam", platform="signal"), timeout=1.0
    )


async def test_full_subscriber_is_dropped_not_kept_stuck() -> None:
    presenter = Presenter()
    slow = presenter.subscribe()
    for i in range(_QUEUE_MAXSIZE):
        slow.put_nowait(f"stale-{i}")

    assert presenter.subscriber_count == 1
    await presenter.speak("overflow", room="fam", platform="signal")
    # The unresponsive subscriber is evicted so future publishes stay cheap;
    # a fresh subscriber would still receive lines normally.
    assert presenter.subscriber_count == 0


async def test_disconnected_subscriber_can_be_unsubscribed_cleanly() -> None:
    presenter = Presenter()
    queue = presenter.subscribe()
    presenter.unsubscribe(queue)

    await presenter.speak("after disconnect", room="fam", platform="signal")
    assert queue.qsize() == 0
    assert presenter.subscriber_count == 0


async def test_speak_never_raises_even_if_a_subscriber_queue_is_broken() -> None:
    presenter = Presenter()
    presenter.subscribe()

    class _ExplodingSet(set):
        def __iter__(self):  # noqa: ANN204 — deliberately breaks the loop in speak()
            raise RuntimeError("boom")

    presenter._subscribers = _ExplodingSet(presenter._subscribers)  # type: ignore[assignment]

    # Must degrade to a logged failure, never an exception into the caller.
    await presenter.speak("should not raise", room="fam", platform="signal")


async def test_start_succeeds_and_stop_is_clean() -> None:
    presenter = Presenter(host="127.0.0.1", port=0)
    ok = await presenter.start()
    assert ok is True
    await presenter.stop()


async def test_second_server_on_the_same_port_degrades_instead_of_raising() -> None:
    first = Presenter(host="127.0.0.1", port=8799)
    second = Presenter(host="127.0.0.1", port=8799)
    try:
        assert await first.start() is True
        # The port is already bound — start() must report failure, not throw.
        assert await second.start() is False
    finally:
        await first.stop()
        await second.stop()


async def test_stop_before_start_does_not_raise() -> None:
    presenter = Presenter()
    await presenter.stop()  # never started; must be a harmless no-op


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
