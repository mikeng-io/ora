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
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

VISION_MODEL = "glm-5.3-flash"

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
) -> Comprehension:
    """A single vision call producing all three fields. `ok=False` on any
    non-`stop` finish or a body that does not parse — an index entry with
    nothing claimed about it is not a failure (design/19 §2)."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": _data_uri(data, media_type)}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    choice = response.choices[0]
    finish_reason = choice.finish_reason or ""
    if finish_reason != "stop":
        return Comprehension(ok=False, finish_reason=finish_reason)
    fields = _parse((choice.message.content or "").strip())
    if fields is None:
        return Comprehension(ok=False, finish_reason=finish_reason)
    title, description, content = fields
    return Comprehension(
        ok=True, title=title, description=description, content=content,
        finish_reason=finish_reason,
    )
