"""One send path, per-platform senders.

Request builders only — Signal's shape from `reference/platform/signal/send.py`
(JSON-RPC `send`, `groupId`/`message`/`mention`), WhatsApp's from
`reference/platform/whatsapp/send.py` (`POST /send`, `jid`/`text`) — **extended**
here with outbound mentions, which the reference project's v1 WhatsApp sender explicitly
does not carry ("outbound WhatsApp mentions are not in v1 scope"). Ora's
own design (`02 §4`) puts them back: `@<jid>` in the text plus a `mentions`
array. The bridge's own wire contract for that is undocumented in this
repo — this is the reasonable shape, unverified until a real send is
made; say so rather than assert it.

Mention resolution (`find_mentions`) is a simplified
`reference/agent/outbound.py::OutboundComposer._scan`: no roster confirmation
(Ora's `people.toml` has one person in v1, not a room to reconcile
against), same longest-match-first, same ASCII/CJK word-boundary rule. An
unresolvable name is left as plain text — never a guess (the reference project's #211).

`speak()` here is the STUB `03 §2` names: it refuses an unlisted room and
builds the request. Landing the row (`is_ora`, `delivery_status`) and the
real HTTP call are event-day (`loop/act.py`'s "policy half", agent A) —
**no real send tonight.**

`react()` is the same JSON-RPC/`POST` endpoint each platform already sends
through, aimed at a reaction instead: WhatsApp's verified `/react` route,
Signal's `sendReaction` (unverified shape — see its own docstring). A
reaction is decoration on top of a reply that already matters; it never
raises and never blocks the reply it decorates (R-5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loop.config import Config
from loop.people import PeopleDirectory
from loop.store import Store

log = logging.getLogger("ora.act")


def _is_word(ch: str) -> bool:
    """ASCII word character — the boundary is only asserted between two of
    these, so CJK handles match with no separator while `@mike` inside
    `@mikeson` does not (the reference project `outbound._is_word`'s rule)."""
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
    """A built, unsent request — dry-run output."""

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


def build_signal_reaction_request(
    conversation_id: str, target_author: str, target_timestamp: int, emoji: str
) -> Request:
    """A `sendReaction` JSON-RPC request, shaped the same way
    `build_signal_request` shapes `send` (same endpoint, same envelope).

    **UNVERIFIED.** This cannot be checked against a live signal-cli from
    here — there is no way to drive a real reaction and see what comes
    back, exactly the situation `observe_signal.py`'s `getAttachment`
    docstring already admits for its own success shape. `targetAuthor` +
    `targetTimestamp` naming and a plain `groupId` are the reasonable,
    signal-cli-documented shape; they are not proven against this
    deployment. Say so rather than assert it."""
    return Request(
        platform="signal",
        method="rpc",
        url_path="/api/v1/rpc",
        payload={
            "jsonrpc": "2.0",
            "method": "sendReaction",
            "params": {
                "groupId": conversation_id,
                "emoji": emoji,
                "targetAuthor": target_author,
                "targetTimestamp": target_timestamp,
            },
            "id": "ora-react",
        },
    )


def build_whatsapp_reaction_request(conversation_id: str, message_id: str, emoji: str) -> Request:
    """The bridge's verified `/react` route: `{jid, message_id, emoji}`."""
    return Request(
        platform="whatsapp",
        method="post",
        url_path="/react",
        payload={"jid": conversation_id, "message_id": message_id, "emoji": emoji},
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


@dataclass(frozen=True)
class Delivery:
    """What actually happened on the wire, and the row that records it."""

    ok: bool
    request: Request | None = None
    row_id: int | None = None
    delivery_status: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ReactionResult:
    """What happened when `react()` tried to put a reaction on a message —
    the caller logs this (the console grammar), exactly as `Delivery` is
    logged for a send. A reaction is decoration, never the point: `ok=False`
    here must never be treated as a reason to hold or retry the reply it is
    decorating."""

    ok: bool
    reason: str = ""


def speak(
    config: Config,
    platform: str,
    conversation_id: str,
    text: str,
    people: PeopleDirectory,
) -> SpeakResult:
    """Refuses an unlisted room before building anything.
    Builds the request; does **not** send it. `deliver` is the half that
    puts it on the wire."""
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


async def deliver(
    config: Config,
    store: Store,
    platform: str,
    conversation_id: str,
    text: str,
    people: PeopleDirectory,
    *,
    session: Any = None,
    timeout_seconds: float = 15.0,
) -> Delivery:
    """`speak` then put it on the wire, then land the row.

    The allowlist is re-checked here rather than trusted from `speak`,
    because this is the function that actually reaches the network: a
    caller that skipped `speak` must not be able to reach a room
    `rooms.toml` does not list.

    The row is written whether the send succeeded or failed, with
    `delivery_status` saying which. A send that left the process but was
    not recorded is the one outcome the trace cannot explain later, so
    recording is not conditional on success.

    Never raises into the loop (R-5): every failure path returns
    `ok=False` with a reason.
    """
    built = speak(config, platform, conversation_id, text, people)
    if not built.ok or built.request is None:
        return Delivery(ok=False, reason=built.reason)

    request = built.request
    base = config.env.signal_base_url if platform == "signal" else config.env.whatsapp_base_url
    if not base:
        return Delivery(ok=False, request=request, reason=f"no base url for {platform}")

    status = "failed"
    reason = ""
    try:
        status, reason = await _post(
            base + request.url_path,
            request.payload,
            session=session,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (R-5)
        reason = type(exc).__name__

    row_id = None
    try:
        row_id = await store.fetchval(
            """INSERT INTO messages
               (platform, conversation_id, workspace, sender_id, person_id,
                is_ora, delivery_status, ts, body, body_len)
               VALUES ($1,$2,$3,'','',TRUE,$4,$5,$6,$7)
               RETURNING id""",
            platform,
            conversation_id,
            config.workspace,
            status,
            datetime.now(UTC),
            text,
            len(text),
        )
    except Exception as exc:  # noqa: BLE001 — a lost row must not lose the send
        reason = reason or f"row not written: {type(exc).__name__}"

    return Delivery(
        ok=status == "sent",
        request=request,
        row_id=row_id,
        delivery_status=status,
        reason=reason,
    )


async def react(
    config: Config,
    platform: str,
    conversation_id: str,
    emoji: str,
    *,
    platform_message_id: str | None = None,
    author: str | None = None,
    store: Any = None,
    session: Any = None,
    timeout_seconds: float = 15.0,
) -> ReactionResult:
    """Put a reaction on a message. **Never raises** (R-5): a reaction is
    decoration on top of the reply that actually matters, so a failure here
    must never propagate and never cost the reply. Every failure path
    returns `ok=False` with a `reason` the caller can log — it never just
    swallows silently.

    Re-checks the allowlist itself, the same discipline `deliver` uses:
    a caller must not be able to react into a room `rooms.toml` does not
    list.

    Per-platform inputs (both come from the row `observe_signal.py` /
    `observe_whatsapp.py` already wrote — see their `platform_message_id`
    / `sender_id` columns):

    - **whatsapp** needs `platform_message_id` (the bridge's own message
      id). `author` is not used.
    - **signal** needs both `author` (the target message's `sender_id`)
      and `platform_message_id` (the target message's own envelope
      timestamp in ms, stored as text) — Signal reactions target a
      timestamp+author pair, not an opaque id. That shape is **UNVERIFIED**
      against a live signal-cli — see `build_signal_reaction_request`.

    `store`, if given, gets a best-effort trace row in `reactions`
    (never load-bearing, never re-read by a decider) — a failure writing it
    is logged and swallowed, exactly like `deliver`'s "a lost row must not
    lose the send", except here nothing downstream depends on it at all.
    `session` is injectable for testing, exactly as `deliver`'s is.
    """
    result = await _try_react(
        config,
        platform,
        conversation_id,
        emoji,
        platform_message_id=platform_message_id,
        author=author,
        session=session,
        timeout_seconds=timeout_seconds,
    )
    if store is not None:
        try:
            await store.execute(
                """INSERT INTO reactions
                   (platform, conversation_id, platform_message_id, emoji, ok, reason, ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                platform,
                conversation_id,
                platform_message_id,
                emoji,
                result.ok,
                result.reason,
                datetime.now(UTC),
            )
        except Exception:  # noqa: BLE001 — a lost trace row must never cost the reaction result
            log.exception("reactions row not written")
    return result


async def _try_react(
    config: Config,
    platform: str,
    conversation_id: str,
    emoji: str,
    *,
    platform_message_id: str | None,
    author: str | None,
    session: Any,
    timeout_seconds: float,
) -> ReactionResult:
    try:
        if config.room_for(platform, conversation_id) is None:
            return ReactionResult(ok=False, reason="not in rooms.toml")

        if platform == "whatsapp":
            if not platform_message_id:
                return ReactionResult(ok=False, reason="no platform_message_id")
            request = build_whatsapp_reaction_request(conversation_id, platform_message_id, emoji)
            base = config.env.whatsapp_base_url
        elif platform == "signal":
            if not platform_message_id or not author:
                return ReactionResult(ok=False, reason="missing target author/timestamp")
            try:
                target_timestamp = int(platform_message_id)
            except ValueError:
                return ReactionResult(
                    ok=False, reason="platform_message_id is not an integer timestamp"
                )
            request = build_signal_reaction_request(
                conversation_id, author, target_timestamp, emoji
            )
            base = config.env.signal_base_url
        else:
            return ReactionResult(ok=False, reason=f"unknown platform {platform!r}")

        if not base:
            return ReactionResult(ok=False, reason=f"no base url for {platform}")

        status, reason = await _post(
            base + request.url_path,
            request.payload,
            session=session,
            timeout_seconds=timeout_seconds,
        )
        return ReactionResult(ok=status == "sent", reason=reason)
    except Exception as exc:  # noqa: BLE001 — a reaction is decoration; never raises (R-5)
        return ReactionResult(ok=False, reason=type(exc).__name__)


async def _post(
    url: str,
    payload: dict[str, Any],
    *,
    session: Any,
    timeout_seconds: float,
) -> tuple[str, str]:
    """POST `payload` as JSON. Returns `(delivery_status, reason)`.
    `session` is injected so a test can drive this without a network."""
    if session is None:
        import aiohttp

        async with aiohttp.ClientSession() as owned:
            return await _post(url, payload, session=owned, timeout_seconds=timeout_seconds)

    async with session.post(url, json=payload, timeout=timeout_seconds) as response:
        if response.status >= 400:
            return "failed", f"HTTP {response.status}"
        return "sent", ""
