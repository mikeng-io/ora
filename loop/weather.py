"""Hong Kong Observatory Open Data — weather for the tag turn, beside
`route`/`search`. Keyless, documented, verified live (endpoints and shapes
confirmed 2026-09-12 against `data.weather.gov.hk`): `rhrread` (current
conditions), `flw` (local forecast prose), `fnd` (9-day forecast). Adapted
from an earlier private project's HKO client, re-shaped to Ora's own
tool contract rather than carried over: three states (`ok` / `nothing_found`
/ `unavailable`, not that project's `WeatherObservation | None`), a single
flat dict per call (not a dataclass + separate render step), and a single
representative temperature rather than every station — Ora's prompt budget
does not stretch to 18 districts' worth of readings the way that project's did.

Two calls cover what this group actually asks:
* `when="now"` — `rhrread` (temperature, humidity, active warnings) fetched
  together with `flw` for a one-line prose forecast (`forecastDesc`); the
  reading is load-bearing (no `rhrread` reading -> no answer), the prose is
  best-effort (a `flw` miss just drops `conditions`, never sinks the call).
* `when="tomorrow"` (or an explicit `YYYY-MM-DD`) — `fnd`'s 9-day list,
  matched to the requested day.

Trimmed out of the raw payloads: the other ~26 per-district temperature/
rainfall stations, `uvindex`, `icon`/`ForecastIcon` codes, `tcInfo`/
`generalSituation`/`outlook`/`seaTemp`/`soilTemp`, `forecastWind`,
`forecastMaxrh`/`forecastMinrh`, `rainfallLastMonth`/
`rainfallJanuaryToLastMonth` — every field left in is one a chat reply can
actually use; the rest is prompt budget spent on nothing rendered (the
`route.py` `FIELD_MASK` reasoning, applied here to a payload HKO does not
let us mask server-side).

Never raises: timeout, DNS failure, a non-200, a non-JSON body, an
unexpected shape, or an injected fetcher that itself raises all degrade to
`unavailable`, matching `loop/route.py` / `loop/search.py` / `loop/vision.py`.
`status` is one of three, same discipline as those tools: `ok` (fields for
`when` are populated with a real reading), `nothing_found` (HKO answered but
had no reading / no matching forecast day — the provider's own honest
miss), `unavailable` (could not check at all). `loop/tag.py` credits a tool
as grounding only on `status == "ok"`; `nothing_found` and `unavailable`
must never be credited, no matter what a model claims.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

log = logging.getLogger("ora.weather")

HKO_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
TIMEOUT_SECONDS = 10.0
MAX_TEXT_CHARS = 240

# One HKO dataType -> parsed dict (or None on any transport/shape failure).
# Real signature `_get_json` implements; tests inject a fake with the same
# shape so no network runs in the suite.
Fetcher = Callable[[str, str, float], Awaitable[Any | None]]

UNAVAILABLE: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't check the weather just now. Say so — don't guess.",
}

NOTHING_FOUND: dict[str, Any] = {
    "status": "nothing_found",
    "note": "HKO had no reading for that.",
}


@dataclass(frozen=True)
class Weather:
    status: str  # "ok" | "unavailable" | "nothing_found"
    when: str = "now"
    date: str = ""
    week: str = ""
    observed_at: str = ""
    temperature_c: float | None = None
    humidity_percent: int | None = None
    max_temp_c: float | None = None
    min_temp_c: float | None = None
    conditions: str = ""
    rain_chance: str = ""
    warnings: tuple[str, ...] = ()


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _data_list(section: Any) -> list[dict[str, Any]]:
    """The `data` list inside a `{recordTime, data:[...]}` HKO section, or []."""
    if not isinstance(section, dict):
        return []
    data = section.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _representative(entries: list[dict[str, Any]]) -> float | None:
    """One reading for a chat reply, not all ~18-27 districts: the Observatory
    headquarters station when it is present, else the first valid reading."""
    first: float | None = None
    for item in entries:
        value = _number(item.get("value"))
        if value is None:
            continue
        if first is None:
            first = value
        if item.get("place") == "Hong Kong Observatory":
            return value
    return first


def _warnings(value: Any) -> tuple[str, ...]:
    """HKO's own quirk: a LIST of strings when warnings are in force, a bare
    `""` when none are — never a guess either way."""
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(v).strip() for v in value if isinstance(v, str) and v.strip())
    return ()


def _iso_date(value: Any) -> str:
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return _text(value, 10)


def read_current(rhrread: Any, flw: Any) -> Weather | None:
    """`rhrread` (load-bearing) + `flw` (best-effort prose) -> one `Weather`,
    or `None` when there is no real reading at all (`nothing_found`)."""
    if not isinstance(rhrread, dict):
        return None
    temperature = _representative(_data_list(rhrread.get("temperature")))
    if temperature is None:
        return None
    humidity_raw = _representative(_data_list(rhrread.get("humidity")))
    temp_block = rhrread.get("temperature")
    observed_at = (
        _text(temp_block.get("recordTime"), 40) if isinstance(temp_block, dict) else ""
    ) or _text(rhrread.get("updateTime"), 40)
    conditions = _text(flw.get("forecastDesc")) if isinstance(flw, dict) else ""
    return Weather(
        status="ok",
        when="now",
        observed_at=observed_at,
        temperature_c=temperature,
        humidity_percent=int(humidity_raw) if humidity_raw is not None else None,
        conditions=conditions,
        warnings=_warnings(rhrread.get("warningMessage")),
    )


def _forecast_index(days: list[dict[str, Any]], when: str) -> int | None:
    if when == "tomorrow":
        return 0
    try:
        target = datetime.strptime(when, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return None
    for i, day in enumerate(days):
        if isinstance(day, dict) and day.get("forecastDate") == target:
            return i
    return None


def read_forecast(fnd: Any, when: str) -> Weather | None:
    """`fnd.weatherForecast`, matched to `when` (`"tomorrow"` or an explicit
    `YYYY-MM-DD`) -> one `Weather`, or `None` when the list is empty or has
    no matching day (`nothing_found`)."""
    if not isinstance(fnd, dict):
        return None
    days = fnd.get("weatherForecast")
    if not isinstance(days, list) or not days:
        return None
    index = _forecast_index(days, when)
    if index is None or index >= len(days):
        return None
    day = days[index]
    if not isinstance(day, dict):
        return None
    max_temp = day.get("forecastMaxtemp")
    min_temp = day.get("forecastMintemp")
    return Weather(
        status="ok",
        when=when,
        date=_iso_date(day.get("forecastDate")),
        week=_text(day.get("week"), 20),
        conditions=_text(day.get("forecastWeather")),
        max_temp_c=_number(max_temp.get("value")) if isinstance(max_temp, dict) else None,
        min_temp_c=_number(min_temp.get("value")) if isinstance(min_temp, dict) else None,
        rain_chance=_text(day.get("PSR"), 20),
    )


def _to_dict(w: Weather) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "ok", "when": w.when}
    if w.when == "now":
        if w.observed_at:
            out["observed_at"] = w.observed_at
        out["temperature_c"] = w.temperature_c
        if w.humidity_percent is not None:
            out["humidity_percent"] = w.humidity_percent
        if w.conditions:
            out["conditions"] = w.conditions
        out["warnings"] = list(w.warnings)  # always present, may be []
        return out
    out["date"] = w.date
    out["week"] = w.week
    if w.conditions:
        out["conditions"] = w.conditions
    out["max_temp_c"] = w.max_temp_c
    out["min_temp_c"] = w.min_temp_c
    if w.rain_chance:
        out["rain_chance"] = w.rain_chance
    return out


async def _get_json(data_type: str, lang: str, timeout_seconds: float) -> Any | None:
    """One HKO `dataType` as parsed JSON, or `None` on any non-200, non-JSON,
    or transport failure. Never raises."""
    params = {"dataType": data_type, "lang": lang}
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(HKO_URL, params=params) as response,
        ):
            text = await response.text()
            if response.status != 200:
                log.warning("hko %s answered %s", data_type, response.status)
                return None
            try:
                return json.loads(text)
            except ValueError:
                log.warning("hko %s did not return JSON", data_type)
                return None
    except Exception as exc:  # noqa: BLE001 — never raises into the loop
        log.warning("hko %s unreachable: %s", data_type, exc)
        return None


async def _fetch_all(
    data_types: list[str], *, lang: str, timeout_seconds: float, fetch: Fetcher
) -> dict[str, Any | None]:
    results = await asyncio.gather(
        *(_call(fetch, dt, lang, timeout_seconds) for dt in data_types),
    )
    return dict(zip(data_types, results, strict=True))


async def _call(fetch: Fetcher, data_type: str, lang: str, timeout_seconds: float) -> Any | None:
    """Defense in depth: even a misbehaving injected `fetch` cannot raise
    into the caller."""
    try:
        return await fetch(data_type, lang, timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — never raises into the loop
        log.warning("hko %s fetch raised: %s", data_type, exc)
        return None


async def weather(
    *,
    when: str = "now",
    lang: str = "en",
    timeout_seconds: float = TIMEOUT_SECONDS,
    fetch: Fetcher | None = None,
) -> dict[str, Any]:
    """`status` is one of three: `ok` (`when`'s fields hold a real reading),
    `nothing_found` (HKO answered but had nothing for `when`), `unavailable`
    (could not check — never report either as "no rain"). `when` is `"now"`,
    `"tomorrow"`, or an explicit `YYYY-MM-DD` within the 9-day window. Never
    raises."""
    fetcher = fetch or _get_json
    if when == "now":
        got = await _fetch_all(
            ["rhrread", "flw"], lang=lang, timeout_seconds=timeout_seconds, fetch=fetcher
        )
        if got.get("rhrread") is None:
            return dict(UNAVAILABLE)
        result = read_current(got.get("rhrread"), got.get("flw"))
    else:
        got = await _fetch_all(["fnd"], lang=lang, timeout_seconds=timeout_seconds, fetch=fetcher)
        if got.get("fnd") is None:
            return dict(UNAVAILABLE)
        result = read_forecast(got.get("fnd"), when)
    if result is None:
        return dict(NOTHING_FOUND)
    return _to_dict(result)
