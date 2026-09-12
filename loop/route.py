"""Google Directions (Routes API v2) — a real route for step 2, tag turn
only, beside Exa. Shape from `reference/infra/routes/{client,
directions,http}.py`, trimmed: place NAMES go straight into `origin.address`/
`destination.address` on the Routes v2 request — the reference project's separate Places-API
resolver is out of scope for one demo route, since v2 accepts an address
directly. Never raises; three states (`ok` / `no_route` / `unavailable`),
same discipline as `reference.infra.transit`/`reference.infra.routes`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger("ora.routes")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TIMEOUT_SECONDS = 8.0

# Only what is rendered — every field is billed (the reference project's `ROUTES_FIELD_MASK`
# docstring: `X-Goog-FieldMask: *` would request, and bill for, the polyline
# and every per-step instruction).
FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.legs.steps.transitDetails.transitLine.nameShort,"
    "routes.legs.steps.transitDetails.transitLine.name,"
    "routes.legs.steps.transitDetails.transitLine.vehicle.type,"
    "routes.legs.steps.transitDetails.stopDetails.departureStop.name,"
    "routes.legs.steps.transitDetails.stopDetails.arrivalStop.name,"
    "routes.legs.steps.transitDetails.headsign"
)

MAX_STEPS = 8
MAX_NAME_CHARS = 80


@dataclass(frozen=True)
class TransitLeg:
    line: str
    vehicle: str
    departure_stop: str
    arrival_stop: str
    headsign: str


@dataclass(frozen=True)
class Journey:
    duration_seconds: int | None
    distance_meters: int | None
    legs: tuple[TransitLeg, ...] = ()


def _text(value: Any, limit: int = MAX_NAME_CHARS) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _duration_seconds(value: Any) -> int | None:
    """`"358s"` -> 358. Anything else is `None` rather than a guess."""
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return int(value[:-1])
    except ValueError:
        return None


def _one_leg(details: dict[str, Any]) -> TransitLeg:
    line = details.get("transitLine") if isinstance(details.get("transitLine"), dict) else {}
    vehicle = line.get("vehicle") if isinstance(line.get("vehicle"), dict) else {}
    stops = details.get("stopDetails") if isinstance(details.get("stopDetails"), dict) else {}
    dep = stops.get("departureStop") if isinstance(stops.get("departureStop"), dict) else {}
    arr = stops.get("arrivalStop") if isinstance(stops.get("arrivalStop"), dict) else {}
    return TransitLeg(
        line=_text(line.get("nameShort")) or _text(line.get("name")),
        vehicle=_text(vehicle.get("type")),
        departure_stop=_text(dep.get("name")),
        arrival_stop=_text(arr.get("name")),
        headsign=_text(details.get("headsign")),
    )


def read_journey(payload: Any) -> Journey | None:
    """The first `route` in a computeRoutes envelope -> one `Journey`, or
    `None` when there is nothing to read (`no_route`)."""
    if not isinstance(payload, dict):
        return None
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        return None
    route = routes[0]
    if not isinstance(route, dict):
        return None
    legs: list[TransitLeg] = []
    for leg in route.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        for step in leg.get("steps") or []:
            if not isinstance(step, dict):
                continue
            details = step.get("transitDetails")
            if isinstance(details, dict):
                legs.append(_one_leg(details))
            if len(legs) >= MAX_STEPS:
                break
    return Journey(
        duration_seconds=_duration_seconds(route.get("duration")),
        distance_meters=route.get("distanceMeters")
        if isinstance(route.get("distanceMeters"), int)
        else None,
        legs=tuple(legs),
    )


async def _post(api_key: str, body: dict[str, Any], timeout_seconds: float) -> Any | None:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(ROUTES_URL, json=body, headers=headers) as response,
        ):
            text = await response.text()
            if response.status != 200:
                log.warning("routes answered %s: %s", response.status, text[:200])
                return None
            try:
                return json.loads(text)
            except ValueError:
                log.warning("routes did not return JSON")
                return None
    except Exception as exc:  # noqa: BLE001 — never raises into the loop
        log.warning("routes unreachable: %s", exc)
        return None


async def route(
    *,
    api_key: str,
    origin: str,
    destination: str,
    mode: str = "TRANSIT",
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """`status` is one of three: `ok` (`journey` holds duration/distance/legs),
    `no_route` (real places, nothing connects them), `unavailable` (could not
    check — never report either as "no route"). Never raises."""
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": mode,
        "computeAlternativeRoutes": False,
        "languageCode": "en-HK",
        "regionCode": "HK",
    }
    payload = await _post(api_key, body, timeout_seconds)
    if payload is None:
        return {
            "status": "unavailable",
            "note": "I couldn't check the route just now. Say so — don't guess a time.",
        }
    journey = read_journey(payload)
    if journey is None:
        return {"status": "no_route", "note": "Google found no route between those places."}
    return {
        "status": "ok",
        "duration_seconds": journey.duration_seconds,
        "distance_meters": journey.distance_meters,
        "legs": [
            {
                "line": leg.line,
                "vehicle": leg.vehicle,
                "departure_stop": leg.departure_stop,
                "arrival_stop": leg.arrival_stop,
                "headsign": leg.headsign,
            }
            for leg in journey.legs
        ],
    }
