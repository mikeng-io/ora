"""One comprehension pass per image, ORA-18: three fields, one call
(design/19-media-comprehension.md §2's shape) — `title` (what the thing
IS), `description` (what it SHOWS), `content` (legible text in it) — all
from a single `glm-5.3-flash` call on the SAME Ollama Cloud endpoint Ora's
deciders already use. Never a second endpoint (ORA-5 holds); never called
for a verdict (ORA-14's "one model everywhere" is narrowed, not broken —
see design/06 ORA-18).

Runs INLINE, not spawned: one flash call on one image measured low
seconds live (design/06 ORA-18) — a queue would solve a problem this
scope does not have. All three empty is an index entry, not a claim of
nothing (Nora's rule, kept): a comprehension failure never blocks the
image from being stored or the message from landing.

Every call writes a `model_calls` row too (ORA-17), the same trace
discipline `loop/model.py` holds for every decider call — a wrong or
missing description is one join away from its prompt digest and latency.
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
) -> Comprehension:
    """A single vision call producing all three fields. `ok=False` on any
    non-`stop` finish, an unparseable body, or a timeout — an index entry
    with nothing claimed about it is not a failure (design/19 §2)."""
    prompt_sha = hashlib.sha256((_SYSTEM + media_type).encode()).hexdigest()[:12]
    started_at = datetime.now(UTC)
    start = time.monotonic()

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
                            {
                                "type": "image_url",
                                "image_url": {"url": _data_uri(data, media_type)},
                            },
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        latency_ms = int((time.monotonic() - start) * 1000)
        call_id = await _record(
            store, model=model, prompt_sha=prompt_sha, latency_ms=latency_ms,
            finish_reason="timeout", ok=False, output_chars=None, started_at=started_at,
        )
        return Comprehension(ok=False, finish_reason="timeout", call_id=call_id)

    latency_ms = int((time.monotonic() - start) * 1000)
    choice = response.choices[0]
    finish_reason = choice.finish_reason or ""
    content = (choice.message.content or "").strip()

    if finish_reason != "stop":
        call_id = await _record(
            store, model=model, prompt_sha=prompt_sha, latency_ms=latency_ms,
            finish_reason=finish_reason, ok=False, output_chars=len(content),
            started_at=started_at,
        )
        return Comprehension(ok=False, finish_reason=finish_reason, call_id=call_id)

    fields = _parse(content)
    if fields is None:
        call_id = await _record(
            store, model=model, prompt_sha=prompt_sha, latency_ms=latency_ms,
            finish_reason=finish_reason, ok=False, output_chars=len(content),
            started_at=started_at,
        )
        return Comprehension(ok=False, finish_reason=finish_reason, call_id=call_id)

    title, description, content_field = fields
    call_id = await _record(
        store, model=model, prompt_sha=prompt_sha, latency_ms=latency_ms,
        finish_reason=finish_reason, ok=True, output_chars=len(content),
        started_at=started_at,
    )
    return Comprehension(
        ok=True, title=title, description=description, content=content_field,
        finish_reason=finish_reason, call_id=call_id,
    )


async def _record(
    store: Store | None,
    *,
    model: str,
    prompt_sha: str,
    latency_ms: int,
    finish_reason: str,
    ok: bool,
    output_chars: int | None,
    started_at: datetime,
) -> int | None:
    if store is None:
        return None
    return await store.fetchval(
        """INSERT INTO model_calls
           (stage, model, reasoning, prompt_sha, prompt_chars, latency_ms,
            finish_reason, ok, output_chars, started_at)
           VALUES ('vision',$1,'none',$2,0,$3,$4,$5,$6,$7)
           RETURNING id""",
        model, prompt_sha, latency_ms, finish_reason, ok, output_chars, started_at,
    )
