"""Fake-transport coverage for loop/weather.py. No network in this suite —
the orchestration tests inject a fake `fetch` (the same shape `_get_json`
has); a couple of tests monkeypatch `aiohttp.ClientSession` directly to pin
the real transport path, the way tests/test_search.py does. The `rhrread`/
`flw`/`fnd` fixtures below are real HKO bodies (trimmed) captured live
2026-09-12 against `data.weather.gov.hk`, so the parser is pinned against
the API's actual shape, not an assumption of it.
"""

from __future__ import annotations

import json

import pytest

from loop.weather import (
    _get_json,
    read_current,
    read_forecast,
    weather,
)

# --- real-shaped fixtures, captured live 2026-09-12 -------------------------

RHRREAD = {
    "rainfall": {
        "data": [
            {"unit": "mm", "place": "Central & Western District", "max": 0, "main": "FALSE"},
            {"unit": "mm", "place": "Eastern District", "max": 0, "main": "FALSE"},
        ],
        "startTime": "2026-09-12T10:45:00+08:00",
        "endTime": "2026-09-12T11:45:00+08:00",
    },
    "icon": [51],
    "iconUpdateTime": "2026-09-12T11:40:00+08:00",
    "uvindex": {"data": [{"place": "King's Park", "value": 7, "desc": "high"}]},
    "updateTime": "2026-09-12T12:02:00+08:00",
    "temperature": {
        "data": [
            {"place": "King's Park", "value": 31, "unit": "C"},
            {"place": "Hong Kong Observatory", "value": 31, "unit": "C"},
            {"place": "Tai Po", "value": 32, "unit": "C"},
        ],
        "recordTime": "2026-09-12T12:00:00+08:00",
    },
    "warningMessage": "",
    "rainfallFrom00To12": (
        "From midnight to noon, no rainfall was recorded at the Hong Kong Observatory."
    ),
    "humidity": {
        "recordTime": "2026-09-12T12:00:00+08:00",
        "data": [{"unit": "percent", "value": 59, "place": "Hong Kong Observatory"}],
    },
}

FLW = {
    "generalSituation": "The northeast monsoon is affecting southern China.",
    "tcInfo": "A tropical depression is centred east-southeast of Da Nang.",
    "fireDangerWarning": "",
    "forecastPeriod": "Weather forecast for this afternoon and tonight",
    "forecastDesc": "Sunny periods in the afternoon. Becoming cloudy with a few "
    "showers and thunderstorms later.",
    "outlook": "Windy at first tomorrow.",
    "updateTime": "2026-09-12T12:45:00+08:00",
}

FND = {
    "generalSituation": "The northeast monsoon will persist over southern China.",
    "weatherForecast": [
        {
            "forecastDate": "20260913",
            "week": "Sunday",
            "forecastWind": "East force 4 to 5.",
            "forecastWeather": "Mainly cloudy with a few showers. Squally "
            "thunderstorms at first.",
            "forecastMaxtemp": {"value": 29, "unit": "C"},
            "forecastMintemp": {"value": 25, "unit": "C"},
            "forecastMaxrh": {"value": 95, "unit": "percent"},
            "forecastMinrh": {"value": 75, "unit": "percent"},
            "ForecastIcon": 63,
            "PSR": "High",
        },
        {
            "forecastDate": "20260914",
            "week": "Monday",
            "forecastWind": "East to northeast force 3 to 4.",
            "forecastWeather": "Sunny intervals. A few showers and isolated "
            "thunderstorms in the morning and at night.",
            "forecastMaxtemp": {"value": 31, "unit": "C"},
            "forecastMintemp": {"value": 26, "unit": "C"},
            "forecastMaxrh": {"value": 95, "unit": "percent"},
            "forecastMinrh": {"value": 65, "unit": "percent"},
            "ForecastIcon": 54,
            "PSR": "Medium Low",
        },
    ],
    "updateTime": "2026-09-12T11:50:00+08:00",
    "seaTemp": {"place": "North Point", "value": 28, "unit": "C"},
    "soilTemp": [{"place": "Hong Kong Observatory", "value": 29.8, "unit": "C"}],
}


# --- pure parsing -----------------------------------------------------------


def test_read_current_parses_the_real_shaped_payload() -> None:
    w = read_current(RHRREAD, FLW)
    assert w is not None
    assert w.status == "ok"
    assert w.temperature_c == 31.0  # Hong Kong Observatory reading, not King's Park
    assert w.humidity_percent == 59
    assert w.observed_at == "2026-09-12T12:00:00+08:00"
    assert "Sunny periods" in w.conditions
    assert w.warnings == ()  # HKO's "no warning" shape is a bare ""


def test_read_current_warning_message_as_list() -> None:
    payload = {**RHRREAD, "warningMessage": ["The Very Hot Weather Warning is now in force."]}
    w = read_current(payload, FLW)
    assert w is not None
    assert w.warnings == ("The Very Hot Weather Warning is now in force.",)


def test_read_current_prefers_observatory_over_first_station() -> None:
    payload = {
        "temperature": {
            "data": [
                {"place": "Elsewhere", "value": 99},
                {"place": "Hong Kong Observatory", "value": 31},
            ]
        }
    }
    w = read_current(payload, {})
    assert w is not None
    assert w.temperature_c == 31.0


def test_read_current_falls_back_to_first_station_when_no_observatory() -> None:
    payload = {"temperature": {"data": [{"place": "Elsewhere", "value": 28}]}}
    w = read_current(payload, {})
    assert w is not None
    assert w.temperature_c == 28.0


def test_read_current_missing_temperature_data_is_none() -> None:
    assert read_current({"temperature": {"data": []}}, {}) is None
    assert read_current({}, {}) is None
    assert read_current(None, {}) is None


def test_read_current_survives_a_broken_flw() -> None:
    w = read_current(RHRREAD, "not a dict")
    assert w is not None
    assert w.conditions == ""  # best-effort piece just drops out


def test_read_forecast_parses_the_real_shaped_payload_for_tomorrow() -> None:
    w = read_forecast(FND, "tomorrow")
    assert w is not None
    assert w.status == "ok"
    assert w.date == "2026-09-13"
    assert w.week == "Sunday"
    assert w.max_temp_c == 29.0
    assert w.min_temp_c == 25.0
    assert w.rain_chance == "High"
    assert "showers" in w.conditions


def test_read_forecast_selects_an_explicit_date_not_just_tomorrow() -> None:
    w = read_forecast(FND, "2026-09-14")
    assert w is not None
    assert w.date == "2026-09-14"
    assert w.rain_chance == "Medium Low"


def test_read_forecast_date_outside_the_9_day_window_is_none() -> None:
    assert read_forecast(FND, "2099-01-01") is None


def test_read_forecast_empty_list_is_none_not_a_false_ok() -> None:
    assert read_forecast({"weatherForecast": []}, "tomorrow") is None
    assert read_forecast({}, "tomorrow") is None
    assert read_forecast(None, "tomorrow") is None


# --- orchestration (injected fetch, no network) -----------------------------


async def _fake_fetch(rhrread=None, flw=None, fnd=None, raise_on: str | None = None):
    bodies = {"rhrread": rhrread, "flw": flw, "fnd": fnd}

    async def fetch(data_type: str, lang: str, timeout_seconds: float):
        if data_type == raise_on:
            raise TimeoutError("simulated timeout")
        return bodies.get(data_type)

    return fetch


async def test_weather_now_ok() -> None:
    fetch = await _fake_fetch(rhrread=RHRREAD, flw=FLW)
    result = await weather(when="now", fetch=fetch)
    assert result["status"] == "ok"
    assert result["temperature_c"] == 31.0
    assert result["warnings"] == []


async def test_weather_tomorrow_ok() -> None:
    fetch = await _fake_fetch(fnd=FND)
    result = await weather(when="tomorrow", fetch=fetch)
    assert result["status"] == "ok"
    assert result["date"] == "2026-09-13"
    assert result["rain_chance"] == "High"


async def test_weather_now_missing_reading_is_nothing_found() -> None:
    fetch = await _fake_fetch(rhrread={"temperature": {"data": []}}, flw=FLW)
    result = await weather(when="now", fetch=fetch)
    assert result["status"] == "nothing_found"


async def test_weather_tomorrow_empty_forecast_is_nothing_found() -> None:
    fetch = await _fake_fetch(fnd={"weatherForecast": []})
    result = await weather(when="tomorrow", fetch=fetch)
    assert result["status"] == "nothing_found"


async def test_weather_fetch_returning_none_is_unavailable() -> None:
    fetch = await _fake_fetch(rhrread=None)
    result = await weather(when="now", fetch=fetch)
    assert result["status"] == "unavailable"


async def test_weather_timeout_is_unavailable_not_a_raise() -> None:
    fetch = await _fake_fetch(raise_on="rhrread")
    result = await weather(when="now", fetch=fetch)
    assert result["status"] == "unavailable"


async def test_weather_transport_error_is_unavailable_not_a_raise() -> None:
    async def fetch(data_type: str, lang: str, timeout_seconds: float):
        raise OSError("connection reset")

    result = await weather(when="tomorrow", fetch=fetch)
    assert result["status"] == "unavailable"


async def test_weather_unexpected_top_level_shape_never_raises_or_reads_ok() -> None:
    """A fetcher that hands back something parsed but not a dict at all (an
    unexpected envelope) never raises. It reads as `nothing_found` — a
    reachable, JSON-shaped answer with nothing usable in it — the same way
    `loop/route.py`'s `read_journey` maps a non-dict payload to `no_route`
    rather than `unavailable`; a real transport/JSON failure is what maps to
    `unavailable` (see the `_get_json`-level tests below)."""

    async def fetch(data_type: str, lang: str, timeout_seconds: float):
        return ["not", "a", "dict"]

    result = await weather(when="now", fetch=fetch)
    assert result["status"] == "nothing_found"


# --- field trimming ----------------------------------------------------------


async def test_now_trims_the_payload_down_to_what_a_reply_needs() -> None:
    fetch = await _fake_fetch(rhrread=RHRREAD, flw=FLW)
    result = await weather(when="now", fetch=fetch)
    dropped = {
        "rainfall", "icon", "iconUpdateTime", "uvindex", "rainfallFrom00To12",
        "generalSituation", "tcInfo", "outlook", "forecastPeriod",
    }
    assert dropped.isdisjoint(result.keys())


async def test_tomorrow_trims_the_payload_down_to_what_a_reply_needs() -> None:
    fetch = await _fake_fetch(fnd=FND)
    result = await weather(when="tomorrow", fetch=fetch)
    dropped = {
        "forecastWind", "forecastMaxrh", "forecastMinrh", "ForecastIcon",
        "generalSituation", "seaTemp", "soilTemp", "updateTime",
    }
    assert dropped.isdisjoint(result.keys())


# --- the real transport path (`_get_json`), monkeypatched like test_search.py ---


class _FakeResponse:
    def __init__(self, status: int, body) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error

    def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_get_json_non_200_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = _FakeSession(_FakeResponse(500, {}))
    monkeypatch.setattr("loop.weather.aiohttp.ClientSession", lambda **kw: fake_session)

    assert await _get_json("rhrread", "en", 5.0) is None


async def test_get_json_non_json_body_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = _FakeSession(_FakeResponse(200, "<html>not json</html>"))
    monkeypatch.setattr("loop.weather.aiohttp.ClientSession", lambda **kw: fake_session)

    assert await _get_json("rhrread", "en", 5.0) is None


async def test_get_json_transport_error_is_none_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = _FakeSession(error=TimeoutError("boom"))
    monkeypatch.setattr("loop.weather.aiohttp.ClientSession", lambda **kw: fake_session)

    assert await _get_json("rhrread", "en", 5.0) is None


async def test_get_json_ok_parses_real_shaped_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = _FakeSession(_FakeResponse(200, RHRREAD))
    monkeypatch.setattr("loop.weather.aiohttp.ClientSession", lambda **kw: fake_session)

    payload = await _get_json("rhrread", "en", 5.0)
    assert isinstance(payload, dict)
    assert payload["temperature"]["data"][1]["place"] == "Hong Kong Observatory"
