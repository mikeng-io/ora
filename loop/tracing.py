"""Langfuse tracing — one place that knows observability exists.

Ora already has a trace of its own: every model-written row points at a
`model_calls` row, and every judgement writes a `decisions` or `gate_log`
row. That answers «what did it decide». Langfuse answers a different
question — «what did the model actually see, and what did it cost» — which
the database deliberately does not store.

Three rules this module holds:

- **Optional.** No keys, no tracing, no error. The agent must run identically
  with Langfuse switched off; an observability dependency that can stop a
  live demo is worse than no observability.
- **Never raises.** Same discipline as `search.py`, `route.py`, `memory.py`,
  `vision.py`: a tracing failure degrades to silence, never into the loop.
- **Phone numbers never leave the machine.** The prompts carry real group
  chat, and Langfuse Cloud is off-box. Masking is narrow by choice (Mike's
  call: numbers only, not the message text) — but a phone number is the one
  thing in a transcript that identifies a real person to a stranger.

The OpenAI drop-in does the actual instrumentation: it captures the model
name, token counts and latency automatically, which is more than hand-rolled
spans would, and it needs no change at any call site.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger("ora.tracing")

# +85212345678, 85212345678, +852 1234 5678 — the shapes a Hong Kong number
# takes in a chat line, including the bare JID/LID digit runs the bridges use.
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def redact_phone_numbers(text: str) -> str:
    """Replace anything phone-shaped with a marker.

    Deliberately greedy about what counts as a number: a false positive
    costs a reader one unreadable token in a trace, a false negative puts a
    real person's number in a third-party service.
    """
    return _PHONE.sub("[phone]", text)


def _mask(data: Any) -> Any:
    """Langfuse mask hook. Walks whatever it is handed — strings, dicts,
    lists — because a chat message reaches Langfuse nested inside the
    OpenAI request body, not as a top-level string."""
    try:
        if isinstance(data, str):
            return redact_phone_numbers(data)
        if isinstance(data, dict):
            return {k: _mask(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return [_mask(v) for v in data]
        return data
    except Exception:  # noqa: BLE001 — masking must never break a trace
        return "[masked]"


def is_configured() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def init() -> Any | None:
    """Start Langfuse if it is configured. Returns the client, or None.

    Must be called AFTER the environment is loaded and BEFORE the OpenAI
    client is created — the drop-in patches at import/instantiation time, so
    the order is load-bearing rather than stylistic.
    """
    if not is_configured():
        log.debug("langfuse not configured; tracing disabled")
        return None
    try:
        from langfuse import Langfuse

        base_url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
        client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=base_url,
            mask=_mask,
            environment="demo",
        )
        log.info("langfuse tracing enabled (%s)", base_url)
        return client
    except Exception as exc:  # noqa: BLE001 — tracing must never stop the agent
        log.warning("langfuse init failed (%s); continuing untraced", type(exc).__name__)
        return None


def traced_openai(*, base_url: str, api_key: str) -> Any:
    """An AsyncOpenAI that reports to Langfuse when it is configured, and a
    plain one when it is not. Same object either way, so nothing downstream
    knows the difference."""
    if is_configured():
        try:
            from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI

            return TracedAsyncOpenAI(base_url=base_url, api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "langfuse openai drop-in unavailable (%s); using plain client",
                type(exc).__name__,
            )
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=base_url, api_key=api_key)


def flush(client: Any | None) -> None:
    """Send anything still buffered. Without this a short-lived run exits
    with its traces still in memory — the single most common way an
    instrumented script produces no traces at all."""
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse flush failed (%s)", type(exc).__name__)
