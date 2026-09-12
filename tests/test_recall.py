"""Coverage for `loop/recall.py`, the Observe/Decide adapter over
`loop/memory.py`'s Honcho client. No network, no real Honcho — a fake
client stands in, in the style of `tests/test_vision.py`'s recording fakes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from loop.recall import (
    Recalled,
    recall_for_turn,
    remember,
)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


@dataclass
class _Recollection:
    reachable: bool
    conclusions: object = ()


@dataclass
class _FakeClient:
    """Records every call it receives; each behaviour knob defaults to the
    happy path so a test only sets what it needs."""

    ai_peer: str = "ora"
    feed_result: object = True
    feed_raises: Exception | None = None
    peer_cards: dict[str, object] = field(default_factory=dict)
    peer_card_raises: Exception | None = None
    representation_result: object = None
    representation_raises: Exception | None = None
    feed_delay: float = 0.0
    peer_card_delay: float = 0.0
    representation_delay: float = 0.0

    feed_calls: list[dict] = field(default_factory=list)
    peer_card_calls: list[dict] = field(default_factory=list)
    representation_calls: list[dict] = field(default_factory=list)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str) -> bool:
        self.feed_calls.append(
            {"peer_id": peer_id, "text": text, "conversation_id": conversation_id}
        )
        if self.feed_delay:
            await asyncio.sleep(self.feed_delay)
        if self.feed_raises is not None:
            raise self.feed_raises
        return self.feed_result

    async def peer_card(self, *, peer_id: str) -> object:
        self.peer_card_calls.append({"peer_id": peer_id})
        if self.peer_card_delay:
            await asyncio.sleep(self.peer_card_delay)
        if self.peer_card_raises is not None:
            raise self.peer_card_raises
        return self.peer_cards.get(peer_id, [])

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 9,
        target: str | None = None,
    ) -> object:
        self.representation_calls.append({"peer_id": peer_id, "search_query": search_query})
        if self.representation_delay:
            await asyncio.sleep(self.representation_delay)
        if self.representation_raises is not None:
            raise self.representation_raises
        if self.representation_result is None:
            return _Recollection(reachable=True, conclusions=())
        return self.representation_result


# --- remember() ---------------------------------------------------------


async def test_remember_feeds_the_message_and_reports_success() -> None:
    client = _FakeClient()

    ok = await remember(
        client,
        platform="signal",
        conversation_id="room-1",
        sender_label="Mike",
        body="hello",
        is_ora=False,
        ts=NOW,
    )

    assert ok is True
    assert client.feed_calls == [
        {"peer_id": "mike", "text": "hello", "conversation_id": "room-1"}
    ]


async def test_remember_uses_the_client_ai_peer_for_ora_own_sends() -> None:
    client = _FakeClient(ai_peer="ora")

    await remember(
        client, platform="signal", conversation_id="room-1", sender_label="Ora",
        body="on it", is_ora=True, ts=NOW,
    )

    assert client.feed_calls[0]["peer_id"] == "ora"


async def test_remember_derives_a_stable_peer_id_across_platforms() -> None:
    """The same display name, fed from two platforms, must land under the
    same Honcho peer — `people.py`'s own rule that a name is the one place
    a human's two ids meet."""
    client = _FakeClient()

    await remember(client, platform="signal", conversation_id="r", sender_label="Mike Ng",
                    body="a", is_ora=False, ts=NOW)
    await remember(client, platform="whatsapp", conversation_id="r", sender_label="Mike Ng",
                    body="b", is_ora=False, ts=NOW)

    peer_ids = {c["peer_id"] for c in client.feed_calls}
    assert len(peer_ids) == 1


async def test_remember_empty_body_is_not_fed() -> None:
    client = _FakeClient()

    ok = await remember(client, platform="signal", conversation_id="r", sender_label="Mike",
                         body="   ", is_ora=False, ts=NOW)

    assert ok is False
    assert client.feed_calls == []


async def test_remember_feed_failure_returns_false_and_does_not_raise() -> None:
    client = _FakeClient(feed_result=False)

    ok = await remember(client, platform="signal", conversation_id="r", sender_label="Mike",
                         body="hi", is_ora=False, ts=NOW)

    assert ok is False


async def test_remember_never_raises_when_client_feed_raises() -> None:
    client = _FakeClient(feed_raises=ConnectionError("down"))

    ok = await remember(client, platform="signal", conversation_id="r", sender_label="Mike",
                         body="hi", is_ora=False, ts=NOW)

    assert ok is False


async def test_remember_a_garbage_feed_result_is_treated_as_failure() -> None:
    """`feed` is documented to return `bool`; a client that hands back
    something truthy-but-not-`True` (a dict, a string) must not be read as
    success."""
    client = _FakeClient(feed_result={"ok": True})

    ok = await remember(client, platform="signal", conversation_id="r", sender_label="Mike",
                         body="hi", is_ora=False, ts=NOW)

    assert ok is False


async def test_remember_degrades_to_empty_on_timeout() -> None:
    client = _FakeClient(feed_delay=0.2)

    ok = await remember(client, platform="signal", conversation_id="r", sender_label="Mike",
                         body="hi", is_ora=False, ts=NOW, timeout_seconds=0.01)

    assert ok is False


# --- recall_for_turn(): self-card ----------------------------------------


async def test_recall_returns_self_conclusions_shaped_for_render_self_card() -> None:
    client = _FakeClient(
        representation_result=_Recollection(reachable=True, conclusions=("said X", "agreed Y"))
    )

    recalled = await recall_for_turn(client, present=[])

    assert isinstance(recalled, Recalled)
    assert recalled.self_conclusions == ["said X", "agreed Y"]
    # shape render_self_card accepts: list[str]
    from loop.render import render_self_card

    assert "<self_card" in render_self_card(recalled.self_conclusions)


async def test_recall_self_card_unreachable_is_empty() -> None:
    client = _FakeClient(representation_result=_Recollection(reachable=False, conclusions=("x",)))

    recalled = await recall_for_turn(client, present=[])

    assert recalled.self_conclusions == []


async def test_recall_self_card_never_raises_when_client_raises() -> None:
    client = _FakeClient(representation_raises=RuntimeError("boom"))

    recalled = await recall_for_turn(client, present=[])

    assert recalled.self_conclusions == []
    assert recalled.peer_facts == []


async def test_recall_self_card_garbage_result_is_empty() -> None:
    for garbage in (None, "a string", 42, {"conclusions": ["x"]}):
        client = _FakeClient(representation_result=garbage)
        recalled = await recall_for_turn(client, present=[])
        assert recalled.self_conclusions == [], garbage


async def test_recall_self_card_conclusions_wrong_type_is_empty() -> None:
    client = _FakeClient(representation_result=_Recollection(reachable=True, conclusions="oops"))

    recalled = await recall_for_turn(client, present=[])

    assert recalled.self_conclusions == []


async def test_recall_self_card_degrades_to_empty_on_timeout() -> None:
    client = _FakeClient(representation_delay=0.2)

    recalled = await recall_for_turn(client, present=[], timeout_seconds=0.01)

    assert recalled.self_conclusions == []


# --- recall_for_turn(): peer cards ----------------------------------------


async def test_recall_returns_a_peer_card_per_present_person() -> None:
    client = _FakeClient(peer_cards={"mike": ["likes coffee"], "alex": ["prefers async"]})

    recalled = await recall_for_turn(client, present=["Mike", "Alex"])

    assert recalled.peer_facts == [("Mike", ["likes coffee"]), ("Alex", ["prefers async"])]
    from loop.render import render_peer_card

    for person, facts in recalled.peer_facts:
        assert "<peer_card" in render_peer_card(person, facts)


async def test_recall_person_with_no_facts_is_absent_from_peer_facts() -> None:
    client = _FakeClient(peer_cards={"mike": []})

    recalled = await recall_for_turn(client, present=["Mike"])

    assert recalled.peer_facts == []


async def test_recall_peer_card_never_raises_when_client_raises() -> None:
    client = _FakeClient(peer_card_raises=RuntimeError("boom"))

    recalled = await recall_for_turn(client, present=["Mike"])

    assert recalled.peer_facts == []


async def test_recall_peer_card_garbage_result_is_empty() -> None:
    client = _FakeClient(peer_cards={"mike": None})

    recalled = await recall_for_turn(client, present=["Mike"])

    assert recalled.peer_facts == []


async def test_recall_peer_card_degrades_to_empty_on_timeout() -> None:
    client = _FakeClient(peer_card_delay=0.2, peer_cards={"mike": ["x"]})

    recalled = await recall_for_turn(client, present=["Mike"], timeout_seconds=0.01)

    assert recalled.peer_facts == []


async def test_recall_dedups_and_preserves_first_seen_order() -> None:
    client = _FakeClient(peer_cards={"mike": ["a"], "alex": ["b"]})

    recalled = await recall_for_turn(client, present=["Mike", "Alex", "Mike"])

    assert [p for p, _ in recalled.peer_facts] == ["Mike", "Alex"]
    assert len(client.peer_card_calls) == 2


# --- caps: count and length -----------------------------------------------


async def test_recall_caps_self_conclusion_count() -> None:
    many = tuple(f"conclusion {i}" for i in range(20))
    client = _FakeClient(representation_result=_Recollection(reachable=True, conclusions=many))

    recalled = await recall_for_turn(client, present=[], max_self_conclusions=3)

    assert len(recalled.self_conclusions) == 3
    assert recalled.self_conclusions == list(many[:3])


async def test_recall_caps_peer_fact_count() -> None:
    client = _FakeClient(peer_cards={"mike": [f"fact {i}" for i in range(20)]})

    recalled = await recall_for_turn(client, present=["Mike"], max_peer_facts=2)

    assert len(recalled.peer_facts[0][1]) == 2


async def test_recall_caps_present_people_fanned_out_to() -> None:
    client = _FakeClient(peer_cards={f"p{i}": [f"fact{i}"] for i in range(20)})
    present = [f"p{i}" for i in range(20)]

    recalled = await recall_for_turn(client, present=present, max_present=3)

    assert len(client.peer_card_calls) == 3
    assert len(recalled.peer_facts) == 3


async def test_recall_caps_line_length_with_ellipsis() -> None:
    long_fact = "x" * 1000
    client = _FakeClient(peer_cards={"mike": [long_fact]})

    recalled = await recall_for_turn(client, present=["Mike"], max_fact_chars=50)

    (_, facts) = recalled.peer_facts[0]
    assert len(facts[0]) == 50
    assert facts[0].endswith("…")


async def test_recall_caps_self_conclusion_line_length() -> None:
    long_conclusion = "y" * 1000
    client = _FakeClient(
        representation_result=_Recollection(reachable=True, conclusions=(long_conclusion,))
    )

    recalled = await recall_for_turn(client, present=[], max_fact_chars=40)

    assert len(recalled.self_conclusions[0]) == 40


# --- independence of the two reads ----------------------------------------


async def test_a_self_card_failure_does_not_lose_peer_cards() -> None:
    client = _FakeClient(
        representation_raises=RuntimeError("boom"),
        peer_cards={"mike": ["still here"]},
    )

    recalled = await recall_for_turn(client, present=["Mike"])

    assert recalled.self_conclusions == []
    assert recalled.peer_facts == [("Mike", ["still here"])]


async def test_a_peer_card_failure_does_not_lose_the_self_card() -> None:
    client = _FakeClient(
        representation_result=_Recollection(reachable=True, conclusions=("kept",)),
        peer_card_raises=RuntimeError("boom"),
    )

    recalled = await recall_for_turn(client, present=["Mike"])

    assert recalled.self_conclusions == ["kept"]
    assert recalled.peer_facts == []
