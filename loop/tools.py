"""The toolbox: one place that runs tools and says what an answer rests on.

Before this module, tools lived inside the tag path alone. The ambient
participation turn and the proactive turn had none, which meant the same
question got a researched answer when Ora was called by name and a guess
when it was not — and `decide_turn` had to reject any `tool:` grounding
outright, because on that path a claimed tool genuinely could not have run.

Every path that can speak now gets the same tools, and — more importantly —
the same rule about them: **a tool is credited only if it actually returned
something.** The model does not get to assert what it used. `run()` executes
the tools, `blocks()` renders exactly what ran into the prompt, and
`validate_grounding()` answers the one question that decides whether Ora is
allowed to speak at all.

No tool-calling protocol: `loop/model.py` is a single-shot JSON client by
design (ORA-14). The caller decides what to look up from the message itself
(`loop/intent.py`), the tools run first, and their results are rendered into
the prompt as untrusted blocks. That is a deliberate trade — the model cannot
go fishing, but it also cannot invent a lookup that never happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from loop.render import escape_attr

log = logging.getLogger("ora.tools")

# A tool answered usefully. Anything else — `unavailable`, `nothing_found`,
# a timeout, a transport error — is a tool that ran and came back empty, and
# must never appear in `grounded_on`.
OK = "ok"


@dataclass(frozen=True)
class ToolRun:
    """What every tool returned this turn, plus what actually grounds.

    `results` holds raw payloads for rendering; `grounded` is the decided
    set of claims a model is permitted to cite. They are separate because a
    tool that returned `unavailable` still belongs in the prompt (so the
    model knows the lookup failed and can say so honestly) while never
    being creditable as grounding.
    """

    results: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    grounded: frozenset[str] = frozenset()

    def ran(self, name: str) -> bool:
        return self.results.get(name) is not None


def _is_ok(result: Any, *, non_empty: str | None = None) -> bool:
    if not isinstance(result, dict) or result.get("status") != OK:
        return False
    if non_empty is None:
        return True
    value = result.get(non_empty)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return value is not None


async def run(
    *,
    route_query: tuple[str, str] | None = None,
    route_mode: str = "TRANSIT",
    route_api_key: str = "",
    route_fn: Any = None,
    search_query: str | None = None,
    search_provider: Any = None,
    fetch_url: str | None = None,
    weather_when: str | None = None,
    weather_fn: Any = None,
    lang: str = "en",
) -> ToolRun:
    """Run whatever the caller asked for. Never raises: each tool is
    already fail-closed on its own, and anything that escapes one is
    recorded as `unavailable` rather than allowed to end the turn — a
    broken tool must cost an answer its citation, not the whole reply."""
    results: dict[str, dict[str, Any] | None] = {}

    if route_query is not None:
        origin, destination = route_query
        if not route_api_key or route_fn is None:
            results["route"] = {"status": "unavailable", "note": "route tool not configured"}
        else:
            try:
                results["route"] = await route_fn(
                    api_key=route_api_key, origin=origin, destination=destination, mode=route_mode
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("route tool failed: %s", type(exc).__name__)
                results["route"] = {"status": "unavailable", "note": type(exc).__name__}

    if search_query is not None:
        if search_provider is None:
            results["search"] = {"status": "unavailable", "note": "search tool not configured"}
        else:
            try:
                results["search"] = await search_provider.search(query=search_query)
            except Exception as exc:  # noqa: BLE001
                log.warning("search tool failed: %s", type(exc).__name__)
                results["search"] = {"status": "unavailable", "note": type(exc).__name__}

    if fetch_url:
        if search_provider is None:
            results["fetch"] = {"status": "unavailable", "note": "fetch tool not configured"}
        else:
            try:
                results["fetch"] = await search_provider.fetch(url=fetch_url)
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch tool failed: %s", type(exc).__name__)
                results["fetch"] = {"status": "unavailable", "note": type(exc).__name__}

    if weather_when is not None:
        if weather_fn is None:
            results["weather"] = {"status": "unavailable", "note": "weather tool not configured"}
        else:
            try:
                results["weather"] = await weather_fn(when=weather_when, lang=lang)
            except Exception as exc:  # noqa: BLE001
                log.warning("weather tool failed: %s", type(exc).__name__)
                results["weather"] = {"status": "unavailable", "note": type(exc).__name__}

    grounded = set()
    if _is_ok(results.get("route"), non_empty="legs"):
        grounded.add("tool:route")
    if _is_ok(results.get("search"), non_empty="results"):
        grounded.add("tool:search")
    if _is_ok(results.get("fetch"), non_empty="content"):
        grounded.add("tool:fetch")
    if _is_ok(results.get("weather")):
        grounded.add("tool:weather")

    return ToolRun(results=results, grounded=frozenset(grounded))


def _block(name: str, result: dict[str, Any] | None, body: str = "") -> str:
    if result is None:
        return ""
    status = escape_attr(str(result.get("status") or "unavailable"))
    if not body:
        return f'<tool name="{name}" status="{status}"></tool>'
    return f'<tool name="{name}" status="{status}" untrusted="true">\n{body}\n</tool>'


def blocks(run_result: ToolRun) -> list[str]:
    """Render only the tools that actually ran.

    A tool that failed is still rendered, with its status — so the model can
    say "I couldn't check" instead of quietly answering as though it had.
    Contents arrive pre-escaped and source-wrapped from their providers and
    are marked untrusted: a fetched page is text a stranger wrote.
    """
    out: list[str] = []
    for name in ("route", "search", "fetch", "weather"):
        result = run_result.results.get(name)
        if result is None:
            continue
        if result.get("status") != OK:
            out.append(_block(name, result))
            continue
        body = _render_body(name, result)
        out.append(_block(name, result, body))
    return [b for b in out if b]


def _render_body(name: str, result: dict[str, Any]) -> str:
    if name == "route":
        legs = result.get("legs") or []
        lines = [
            f"- {leg.get('vehicle', '')} {leg.get('line', '')}: "
            f"{leg.get('departure_stop', '')} -> {leg.get('arrival_stop', '')}"
            for leg in legs
            if isinstance(leg, dict)
        ]
        duration = result.get("duration_seconds")
        if duration:
            lines.insert(0, f"about {int(duration) // 60} minutes")
        return "\n".join(lines)
    if name == "search":
        items = result.get("results") or []
        return "\n".join(str(i.get("content") or "") for i in items if isinstance(i, dict))
    if name == "fetch":
        return str(result.get("content") or "")
    if name == "weather":
        return "\n".join(
            f"{k}: {v}" for k, v in result.items() if k != "status" and v not in (None, "")
        )
    return ""


def validate_grounding(
    claim: Any,
    run_result: ToolRun,
    *,
    standing_present: bool,
    valid_note_ids: frozenset[str],
) -> str | None:
    """The one question that decides whether Ora may speak.

    Checked against what happened, never against what the model says
    happened: a claimed `tool:route` whose lookup came back `no_route` is
    not grounding, and a `note:` id the turn was never shown is a citation
    to nothing. Returns the value to record, or `None` — and a `None` is
    what the callers turn into silence.
    """
    if not isinstance(claim, str) or not claim:
        return None
    if claim == "standing":
        return claim if standing_present else None
    if claim.startswith("note:"):
        return claim if claim[len("note:") :] in valid_note_ids else None
    if claim.startswith("tool:"):
        return claim if claim in run_result.grounded else None
    return None
