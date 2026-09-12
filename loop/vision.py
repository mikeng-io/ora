"""One comprehension pass per image, ORA-18: three fields, one call
(shape) — `title` (what the thing
IS), `description` (what it SHOWS), `content` (legible text in it) — all
from a single `glm-5.3-flash` call on the SAME Ollama Cloud endpoint Ora's
deciders already use. Never a second endpoint (ORA-5 holds); never called
for a verdict (ORA-14's "one model everywhere" is narrowed, not broken —
see ORA-18).

Runs INLINE, not spawned: one flash call on one image measured low
seconds live (ORA-18) — a queue would solve a problem this
scope does not have. All three empty is an index entry, not a claim of
nothing (the reference project's rule, kept): a comprehension failure never blocks the
image from being stored or the message from landing.

**Never raises** — the same discipline `loop/search.py`, `loop/route.py`
and `loop/memory.py` hold: a transport failure, a rate limit, a malformed
response, or an empty `choices` list all degrade to `ok=False`, never an
exception into the caller. (Review finding, Opus: the timeout-only catch
in the first cut left every other real `AsyncOpenAI` failure mode —
`APIConnectionError`, `RateLimitError`, `APIStatusError`, an `IndexError`
on empty `choices` — unhandled here, relying entirely on the caller's own
try/except. Fixed at the source instead of only at the call site.)

Every call writes a `model_calls` row too (ORA-17), the same trace
discipline `loop/model.py` holds for every decider call. `prompt_sha`
folds in the image bytes (not just the fixed system prompt), and
`prompt_chars` is the actual data-URI length sent — a wrong or missing
description is one join away from its real prompt digest and latency,
not a digest shared by every image the run ever sees.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loop.store import Store

VISION_MODEL = "glm-5.3-flash"
DEFAULT_TIMEOUT_SECONDS = 20.0

_SYSTEM = (
    "You describe one image for a group chat's own record. Reply with JSON "
    'only: {"title": "...", "description": "...", "content": "..."}. '
    "title: what the thing IS, a few words, how a person would refer to it. "
    "description: what it SHOWS, a sentence or two. content: any text "
    "legible in the image, verbatim, or an empty string if there is none."
)


@dataclass(frozen=True)
class Comprehension:
    ok: bool
    title: str = ""
    description: str = ""
    content: str = ""
    finish_reason: str = ""
    call_id: int | None = None


def _data_uri(data: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _parse(content: str) -> tuple[str, str, str] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return (
        str(parsed.get("title") or ""),
        str(parsed.get("description") or ""),
        str(parsed.get("content") or ""),
    )


async def comprehend(
    client: Any,  # an AsyncOpenAI, or anything with .chat.completions.create
    *,
    data: bytes,
    media_type: str,
    model: str = VISION_MODEL,
    max_tokens: int = 400,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    store: Store | None = None,
    platform: str | None = None,
    conversation_id: str | None = None,
) -> Comprehension:
    """A single vision call producing all three fields. `ok=False` on any
    non-`stop` finish, an unparseable body, a timeout, or any other
    transport/API failure — an index entry with nothing claimed about it
    is not a failure."""
    data_uri = _data_uri(data, media_type)
    prompt_sha = hashlib.sha256((_SYSTEM + data_uri).encode()).hexdigest()[:12]
    prompt_chars = len(_SYSTEM) + len(data_uri)
    started_at = datetime.now(UTC)
    start = time.monotonic()

    async def record(*, finish_reason: str, ok: bool, output_chars: int | None) -> int | None:
        latency_ms = int((time.monotonic() - start) * 1000)
        return await _record(
            store, model=model, prompt_sha=prompt_sha, prompt_chars=prompt_chars,
            latency_ms=latency_ms, finish_reason=finish_reason, ok=ok,
            output_chars=output_chars, started_at=started_at,
            platform=platform, conversation_id=conversation_id,
        )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image."},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        call_id = await record(finish_reason="timeout", ok=False, output_chars=None)
        return Comprehension(ok=False, finish_reason="timeout", call_id=call_id)
    except Exception as exc:  # noqa: BLE001 — never raises into the caller (R-5)
        call_id = await record(finish_reason=type(exc).__name__, ok=False, output_chars=None)
        return Comprehension(ok=False, finish_reason=type(exc).__name__, call_id=call_id)

    try:
        choice = response.choices[0]
    except (IndexError, AttributeError):
        call_id = await record(finish_reason="empty_choices", ok=False, output_chars=None)
        return Comprehension(ok=False, finish_reason="empty_choices", call_id=call_id)

    finish_reason = choice.finish_reason or ""
    content = (choice.message.content or "").strip()

    if finish_reason != "stop":
        call_id = await record(finish_reason=finish_reason, ok=False, output_chars=len(content))
        return Comprehension(ok=False, finish_reason=finish_reason, call_id=call_id)

    fields = _parse(content)
    if fields is None:
        call_id = await record(finish_reason=finish_reason, ok=False, output_chars=len(content))
        return Comprehension(ok=False, finish_reason=finish_reason, call_id=call_id)

    title, description, content_field = fields
    call_id = await record(finish_reason=finish_reason, ok=True, output_chars=len(content))
    return Comprehension(
        ok=True, title=title, description=description, content=content_field,
        finish_reason=finish_reason, call_id=call_id,
    )


async def _record(
    store: Store | None,
    *,
    model: str,
    prompt_sha: str,
    prompt_chars: int,
    latency_ms: int,
    finish_reason: str,
    ok: bool,
    output_chars: int | None,
    started_at: datetime,
    platform: str | None,
    conversation_id: str | None,
) -> int | None:
    if store is None:
        return None
    return await store.fetchval(
        """INSERT INTO model_calls
           (stage, platform, conversation_id, model, reasoning, prompt_sha,
            prompt_chars, latency_ms, finish_reason, ok, output_chars, started_at)
           VALUES ('vision',$1,$2,$3,'none',$4,$5,$6,$7,$8,$9,$10)
           RETURNING id""",
        platform, conversation_id, model, prompt_sha, prompt_chars, latency_ms,
        finish_reason, ok, output_chars, started_at,
    )
