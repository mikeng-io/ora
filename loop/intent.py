"""Tag turn -> tool intents. `loop/tag.py::handle_tag` takes `route_query`
and `search_query` as plain parameters and never decides them itself (its
own docstring: "Whatever `route_query`/`search_query` the caller wires in
is looked up first") — something upstream of it has to turn the tagged
message into those two optional values. This module is that something.

Pure and dependency-free on purpose: it runs on the hot path of a live
demo, before any model call, so it must never block on the network and
must never raise (CLAUDE.md rule: a tool that never runs is a silent
failure; a lookup module that crashes the tag path is worse than one that
returns nothing). Every branch below degrades to "no intent" rather than
guessing — a wrong destination costs more than a missed one, because the
answer that follows would be confidently about the wrong place.

This is regex-over-keywords, not a model call, and both scripts. The room
is Cantonese and English mixed in one sentence, so every pattern pair
(a Chinese marker, an English marker) is checked independently rather than
translating one into the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words a traveller says instead of a place. Never trust a match here as a
# destination — "how do we get THERE" tells us travel is meant, not where.
_NOT_A_PLACE = {"there", "here", "it", "us", "home", "back"}

# 點去 / 點樣去 / 搭咩車 are the literal phrasings CLAUDE.md's task calls
# out; "幾點去" ("what time do we go") already contains 點去 as a
# substring so it needs no pattern of its own. Kept short and literal
# rather than a broad "去" scan, because 去 alone is also plain narration
# ("我去街市") that carries no question — matching it would fire the tool
# on every mention of going anywhere, not just when someone is asking how.
_TRAVEL_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"點樣去"),
    re.compile(r"點去"),
    re.compile(r"搭咩車"),
    re.compile(r"how (?:do|did|does) (?:we|i|you|they) get (?:to|there)", re.IGNORECASE),
    re.compile(r"how to get (?:to|there)", re.IGNORECASE),
    re.compile(r"getting to\b", re.IGNORECASE),
)

# A destination named right after "go"/"get to". Two alternatives inside
# one capture group, not two separate patterns, so a single scan of the
# text yields correctly ordered matches when a message names more than one
# place (English place name inside a Cantonese sentence, or vice versa).
_DEST_AFTER_GO_ZH = re.compile(
    r"去\s*([A-Za-z][A-Za-z0-9]*(?:[ ][A-Za-z0-9]+)*|[一-鿿]{1,12})"
)
_DEST_AFTER_GET_TO_EN = re.compile(
    r"\bget(?:ting)?\s+to\s+([A-Za-z][A-Za-z0-9]*(?:[ ][A-Za-z0-9]+){0,4})",
    re.IGNORECASE,
)

_ORIGIN_ZH = re.compile(r"(?:從|由)\s*([一-鿿A-Za-z0-9]{1,20})\s*去")
_ORIGIN_EN = re.compile(r"\bfrom\s+([A-Za-z][A-Za-z0-9 ]*?)\s+to\b", re.IGNORECASE)

# A conservative allowlist, not a denylist: requirement 4 says most tagged
# messages need no search at all, so the default for "some question-ish
# text I don't recognise" is silence, not a web call. Each marker is a word
# that names a fact the room's own history cannot supply (a name, a price,
# a reason) rather than a generic question shape ("?" alone matches too
# much — "点去？" is a question and is not a search).
_SEARCH_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[乜咩]嘢"),  # 咩嘢 / 乜嘢 — "what"
    re.compile(r"邊個"),  # "who"
    re.compile(r"幾多"),  # "how much / how many"
    re.compile(r"點解"),  # "why"
    re.compile(r"\bwhat\b", re.IGNORECASE),
    re.compile(r"\bwho\b", re.IGNORECASE),
    re.compile(r"\bwhy\b", re.IGNORECASE),
    re.compile(r"\bwhich\b", re.IGNORECASE),
    re.compile(r"how (?:much|many)\b", re.IGNORECASE),
)

_TAG_RE = re.compile(r"@ora\b", re.IGNORECASE)


@dataclass(frozen=True)
class RouteIntent:
    """Fields line up 1:1 with `route()`'s keyword arguments
    (`loop/route.py`) so a caller can pass this straight through."""

    origin: str
    destination: str
    mode: str = "TRANSIT"


@dataclass(frozen=True)
class Intents:
    route: RouteIntent | None = None
    search: str | None = None  # the query to hand Exa


def _candidates(text: str) -> list[tuple[int, str]]:
    """Every plausible destination mention in `text`, as (position, name),
    in reading order. Position is kept so callers can take "the first
    place named" from a live message or "the last place named" from a
    transcript — recency matters differently in each."""
    found: list[tuple[int, str]] = []
    for pattern in (_DEST_AFTER_GET_TO_EN, _DEST_AFTER_GO_ZH):
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if candidate and candidate.lower() not in _NOT_A_PLACE:
                found.append((match.start(), candidate))
    found.sort(key=lambda pair: pair[0])
    return found


def _first_destination(text: str) -> str | None:
    candidates = _candidates(text)
    return candidates[0][1] if candidates else None


def _last_destination(text: str) -> str | None:
    candidates = _candidates(text)
    return candidates[-1][1] if candidates else None


def _origin(text: str) -> str | None:
    for pattern in (_ORIGIN_EN, _ORIGIN_ZH):
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def _search_query(text: str) -> str | None:
    if not any(pattern.search(text) for pattern in _SEARCH_MARKERS):
        return None
    cleaned = _TAG_RE.sub("", text).strip()
    return cleaned or None


def extract(text: str, *, default_origin: str = "", transcript_text: str = "") -> Intents:
    """One tagged message -> what to look up, or nothing. Never raises:
    every branch below is a plain string/regex operation over `text or
    ""`, so the worst any input (empty, emoji, 5000 characters, no @ora at
    all — detecting the tag itself is `loop.py`'s job, not this module's)
    can do is produce an `Intents()` with both fields `None`.
    """
    text = text or ""
    transcript_text = transcript_text or ""

    has_travel_intent = any(pattern.search(text) for pattern in _TRAVEL_MARKERS)

    route_intent: RouteIntent | None = None
    if has_travel_intent:
        destination = _first_destination(text)
        if destination is None and transcript_text:
            # "點去？" names no place at all — the only "there" it can
            # mean is whatever the room was just discussing, so look in
            # the transcript instead of the tagged line itself. Take the
            # LAST mention (most recent), not the first, because an older
            # message may have moved on to a different place since.
            destination = _last_destination(transcript_text)
        if destination:
            # Origin is almost always unstated ("how do we get there"
            # assumes "from here"): rather than withholding the whole
            # route intent for a merely-ambiguous origin, we hand the
            # caller `default_origin` (documented judgement call — see
            # module report) and, failing that, an empty string. `route()`
            # will itself come back `no_route`/`unavailable` on a bad
            # address; that is a cheaper failure than never trying.
            origin = _origin(text) or default_origin
            route_intent = RouteIntent(origin=origin, destination=destination)

    search_query = _search_query(text)

    return Intents(route=route_intent, search=search_query)
