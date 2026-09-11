"""Pure-function coverage for loop/route.py (design/07 item 13). The live
call is verified separately — see the item's commit for why it is marked
unverified (the GOOGLE_MAP_API_KEY in .env is rejected by Google itself,
even against the plain Geocoding endpoint — not a code defect)."""

from loop.route import _duration_seconds, read_journey


def test_duration_parses_trailing_s() -> None:
    assert _duration_seconds("358s") == 358


def test_duration_none_for_malformed_input() -> None:
    assert _duration_seconds("358") is None
    assert _duration_seconds(None) is None
    assert _duration_seconds("") is None


def test_read_journey_none_for_empty_routes() -> None:
    assert read_journey({"routes": []}) is None
    assert read_journey({}) is None
    assert read_journey(None) is None


def test_read_journey_extracts_duration_distance_and_no_transit_legs() -> None:
    payload = {
        "routes": [
            {"duration": "600s", "distanceMeters": 5000, "legs": [{"steps": []}]}
        ]
    }
    journey = read_journey(payload)
    assert journey is not None
    assert journey.duration_seconds == 600
    assert journey.distance_meters == 5000
    assert journey.legs == ()


def test_read_journey_extracts_a_transit_leg() -> None:
    payload = {
        "routes": [
            {
                "duration": "1800s",
                "distanceMeters": 12000,
                "legs": [
                    {
                        "steps": [
                            {
                                "transitDetails": {
                                    "transitLine": {
                                        "nameShort": "K73",
                                        "vehicle": {"type": "BUS"},
                                    },
                                    "stopDetails": {
                                        "departureStop": {"name": "Tin Shui Wai Stop"},
                                        "arrivalStop": {"name": "Cyberport Stop"},
                                    },
                                    "headsign": "Cyberport",
                                }
                            }
                        ]
                    }
                ],
            }
        ]
    }
    journey = read_journey(payload)
    assert journey is not None
    assert len(journey.legs) == 1
    leg = journey.legs[0]
    assert leg.line == "K73"
    assert leg.vehicle == "BUS"
    assert leg.departure_stop == "Tin Shui Wai Stop"
    assert leg.arrival_stop == "Cyberport Stop"
