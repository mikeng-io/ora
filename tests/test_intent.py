"""Pure-function coverage for loop/intent.py — the module that turns a
tagged message into `handle_tag`'s `route_query`/`search_query`. No
network, no fakes: every case here is a plain string in, an `Intents` out.
"""

from loop.intent import Intents, RouteIntent, extract

# A recent mention of Cyberport in the room — stands in for a real
# transcript when a message says "there" but never names a place itself.
_CYBERPORT_TRANSCRIPT = "amy: 今日去 Cyberport 開會\nben: ok"


def test_demo_line_resolves_to_cyberport() -> None:
    """The literal message this afternoon's demo hinges on. It never names
    a place itself ("點去？" = "how do we get [there]?") so the answer has
    to come from what the room was just discussing."""
    result = extract(
        "@ora 咁聽日 10 點集合，點去？",
        transcript_text=_CYBERPORT_TRANSCRIPT,
    )
    assert result.route is not None
    assert result.route.destination == "Cyberport"


def test_cantonese_destination_named_in_the_message() -> None:
    assert extract("點去 Cyberport？").route == RouteIntent(origin="", destination="Cyberport")


def test_cantonese_destination_in_chinese_script() -> None:
    assert extract("點樣去中環").route == RouteIntent(origin="", destination="中環")


def test_english_get_to_phrasing() -> None:
    result = extract("how do we get to Cyberport")
    assert result.route is not None
    assert result.route.destination == "Cyberport"


def test_搭咩車去_falls_back_to_transcript_for_destination() -> None:
    """搭咩車去 ("which vehicle do we take to get [there]") names no place
    of its own — same "there" gap as the demo line."""
    result = extract("搭咩車去", transcript_text=_CYBERPORT_TRANSCRIPT)
    assert result.route is not None
    assert result.route.destination == "Cyberport"


def test_unidentifiable_destination_yields_no_route_intent() -> None:
    """幾點去 ("what time do we go") carries travel intent but names no
    place, and there is no transcript to fall back on. A guessed
    destination is worse than none, so this must stay `route=None`."""
    assert extract("幾點去").route is None


def test_default_origin_applied_when_origin_unstated() -> None:
    """Origin is almost never stated ("how do we get there" assumes "from
    here"); the caller's `default_origin` fills the gap rather than the
    route intent being withheld over an ambiguous origin."""
    result = extract("點去 Cyberport？", default_origin="Central")
    assert result.route == RouteIntent(origin="Central", destination="Cyberport")


def test_no_default_origin_still_returns_the_intent_with_empty_origin() -> None:
    result = extract("點去 Cyberport？")
    assert result.route is not None
    assert result.route.origin == ""


def test_non_travel_question_yields_no_route() -> None:
    result = extract("邊個係下一屆特首？")
    assert result.route is None


def test_non_travel_question_yields_search() -> None:
    result = extract("邊個係下一屆特首？")
    assert result.search == "邊個係下一屆特首？"


def test_plain_social_messages_need_no_tool() -> None:
    assert extract("lol") == Intents()
    assert extract("ok 聽日見") == Intents()


def test_both_route_and_search_from_one_message() -> None:
    result = extract("點去 Cyberport呀? 仲有幾多蚊車錢?")
    assert result.route is not None
    assert result.route.destination == "Cyberport"
    assert result.search is not None


def test_search_is_conservative_travel_only_message_gets_no_search() -> None:
    """Requirement 4: most tagged messages need no search at all. A pure
    travel question must not also trigger a web lookup."""
    result = extract("how do we get to Cyberport")
    assert result.search is None


def test_never_raises_on_hostile_input() -> None:
    hostile_inputs = [
        "",
        "🎉🎉🎉🚀",
        "x" * 5000,
        "no tag here, just a sentence",
        None,  # type: ignore[arg-type]
        "\n\t   ",
        "点去" * 500,
    ]
    for text in hostile_inputs:
        result = extract(text)  # type: ignore[arg-type]
        assert isinstance(result, Intents)
