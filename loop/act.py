"""One send path, per-platform senders (design/07 item 10; design/01 §2).

Request builders only — Signal's shape from `nora/platform/signal/send.py`
(JSON-RPC `send`, `groupId`/`message`/`mention`), WhatsApp's from
`nora/platform/whatsapp/send.py` (`POST /send`, `jid`/`text`) — **extended**
here with outbound mentions, which Nora's v1 WhatsApp sender explicitly
does not carry ("outbound WhatsApp mentions are not in v1 scope"). Ora's
own design (`02 §4`) puts them back: `@<jid>` in the text plus a `mentions`
array. The bridge's own wire contract for that is undocumented in this
repo — this is the reasonable shape, unverified until a real send is
made; say so rather than assert it.

Mention resolution (`find_mentions`) is a simplified
`nora/agent/outbound.py::OutboundComposer._scan`: no roster confirmation
(Ora's `people.toml` has one person in v1, not a room to reconcile
against), same longest-match-first, same ASCII/CJK word-boundary rule. An
unresolvable name is left as plain text — never a guess (Nora #211).

`speak()` here is the STUB `03 §2` names: it refuses an unlisted room and
builds the request. Landing the row (`is_ora`, `delivery_status`) and the
real HTTP call are event-day (`loop/act.py`'s "policy half", agent A) —
**no real send tonight.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loop.config import Config
from loop.people import PeopleDirectory


def _is_word(ch: str) -> bool:
    """ASCII word character — the boundary is only asserted between two of
    these, so CJK handles match with no separator while `@mike` inside
    `@mikeson` does not (Nora `outbound._is_word`'s rule)."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _utf16_len(text: str) -> int:
    """Code units, not code points — a mention range signal-cli indexes in
    UTF-16, so an emoji or a CJK character outside the BMP would misplace
    every range after it if counted in Python `len()`."""
    return len(text.encode("utf-16-le")) // 2


def find_mentions(text: str, people: PeopleDirectory) -> list[tuple[int, int, str]]:
    """Every `@Name` in `text` as `(index of @, handle length in chars,
    name)`, longest match first, left to right, non-overlapping."""
    names = people.names_longest_first()
    out: list[tuple[int, int, str]] = []
    i = 0
    while (i := text.find("@", i)) != -1:
        if i > 0 and (_is_word(text[i - 1]) or text[i - 1] == "@"):
            i += 1
            continue
        matched: str | None = None
        for name in names:
            end = i + 1 + len(name)
            if text[i + 1 : end].casefold() != name.casefold():
                continue
            if end < len(text) and _is_word(name[-1]) and _is_word(text[end]):
                continue  # `@mike` inside `@mikeson`
            matched = name
            break
        if matched is None:
            i += 1
            continue
        out.append((i, len(matched), matched))
        i += 1 + len(matched)
    return out


@dataclass(frozen=True)
class Request:
    """A built, unsent request — dry-run output for design/07 item 10."""

    platform: str
    method: str  # "rpc" (Signal) | "post" (WhatsApp)
    url_path: str
    payload: dict[str, Any]


def build_signal_request(conversation_id: str, text: str, people: PeopleDirectory) -> Request:
    mentions = []
    for start_char, handle_len, name in find_mentions(text, people):
        recipient = people.reverse("signal", name)
        if recipient is None:
            continue  # unresolvable — plain text, never a guess
        start = _utf16_len(text[:start_char])
        length = _utf16_len(text[start_char : start_char + 1 + handle_len])
        mentions.append(f"{start}:{length}:{recipient}")

    params: dict[str, Any] = {"groupId": conversation_id, "message": text}
    if mentions:
        params["mention"] = mentions
    return Request(
        platform="signal",
        method="rpc",
        url_path="/api/v1/rpc",
        payload={"jsonrpc": "2.0", "method": "send", "params": params, "id": "ora"},
    )


def build_whatsapp_request(conversation_id: str, text: str, people: PeopleDirectory) -> Request:
    mentions = []
    rendered = text
    for start_char, handle_len, name in reversed(find_mentions(text, people)):
        recipient = people.reverse("whatsapp", name)
        if recipient is None:
            continue  # unresolvable — plain text, never a guess
        digits = recipient.split("@", 1)[0]
        rendered = rendered[:start_char] + f"@{digits}" + rendered[start_char + 1 + handle_len :]
        mentions.append(recipient)

    payload: dict[str, Any] = {"jid": conversation_id, "text": rendered}
    if mentions:
        payload["mentions"] = list(reversed(mentions))
    return Request(platform="whatsapp", method="post", url_path="/send", payload=payload)


@dataclass(frozen=True)
class SpeakResult:
    ok: bool
    request: Request | None = None
    reason: str = ""


def speak(
    config: Config,
    platform: str,
    conversation_id: str,
    text: str,
    people: PeopleDirectory,
) -> SpeakResult:
    """Refuses an unlisted room before building anything (design/02 §3).
    Builds the request; does **not** send it — landing the row and the
    real HTTP call are event-day (design/07 item 10's stub)."""
    room = config.room_for(platform, conversation_id)
    if room is None:
        return SpeakResult(ok=False, reason="not in rooms.toml")
    if platform == "signal":
        request = build_signal_request(conversation_id, text, people)
    elif platform == "whatsapp":
        request = build_whatsapp_request(conversation_id, text, people)
    else:
        return SpeakResult(ok=False, reason=f"unknown platform {platform!r}")
    return SpeakResult(ok=True, request=request)
