"""OpenAI-compatible client (Ollama Cloud) — raw `json_object` completions
only, never a structured-output tool call for a verdict (the reference project's #186).

What a raw client owes (`reference/app/composition.py::_RelevanceGateModelClient`):
an explicit `asyncio.wait_for` (the bare client default is 600s x retries and
this call HOLDS the conversation's slot), a `finish_reason` read (a raw
completion returns `"length"` on a cut-off rather than raising), and a usage
annotation. This shape adds one thing Ora needs that the reference project's callers get from
a span: every call, ok or not, becomes a `model_calls` row so
a wrong verdict is one join away from its prompt digest and latency.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loop.store import Store


@dataclass
class ModelCompletion:
    content: str
    finish_reason: str
    ok: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int
    call_id: int | None
    error_kind: str | None = None


class ModelClient:
    """One client, one model, everywhere (ORA-14). `reasoning_effort` is
    pinned per call by the caller — never left to a route default."""

    def __init__(
        self,
        *,
        client: Any,  # an AsyncOpenAI, or anything with .chat.completions.create
        model: str,
        timeout_seconds: float = 20.0,
        store: Store | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._store = store

    @property
    def model(self) -> str:
        return self._model

    async def complete_json(
        self,
        *,
        stage: str,
        system: str,
        prompt: str,
        reasoning_effort: str,
        platform: str | None = None,
        conversation_id: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> ModelCompletion:
        prompt_sha = hashlib.sha256((system + prompt).encode()).hexdigest()[:12]
        prompt_chars = len(system) + len(prompt)
        started_at = datetime.now(UTC)
        start = time.monotonic()

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                ),
                timeout=timeout_seconds if timeout_seconds is not None else self._timeout_seconds,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            call_id = await self._record(
                stage=stage,
                platform=platform,
                conversation_id=conversation_id,
                reasoning=reasoning_effort,
                prompt_sha=prompt_sha,
                prompt_chars=prompt_chars,
                prompt_tokens=None,
                completion_tokens=None,
                reasoning_tokens=None,
                latency_ms=latency_ms,
                finish_reason="timeout",
                ok=False,
                output_chars=None,
                started_at=started_at,
            )
            return ModelCompletion(
                content="",
                finish_reason="timeout",
                ok=False,
                prompt_tokens=None,
                completion_tokens=None,
                reasoning_tokens=None,
                latency_ms=latency_ms,
                call_id=call_id,
                error_kind="timeout",
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        choice = response.choices[0]
        # `or ""` and not a "stop" default: an omitted field has told us
        # nothing, and inventing "stop" would assert the call completed.
        finish_reason = choice.finish_reason or ""
        content = (choice.message.content or "").strip()
        ok = finish_reason == "stop"

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        reasoning_tokens = None
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        if details is not None:
            reasoning_tokens = getattr(details, "reasoning_tokens", None)

        call_id = await self._record(
            stage=stage,
            platform=platform,
            conversation_id=conversation_id,
            reasoning=reasoning_effort,
            prompt_sha=prompt_sha,
            prompt_chars=prompt_chars,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            ok=ok,
            output_chars=len(content),
            started_at=started_at,
        )

        return ModelCompletion(
            content=content,
            finish_reason=finish_reason,
            ok=ok,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            call_id=call_id,
            error_kind=None if ok else (finish_reason or "error"),
        )

    async def _record(
        self,
        *,
        stage: str,
        platform: str | None,
        conversation_id: str | None,
        reasoning: str,
        prompt_sha: str,
        prompt_chars: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        reasoning_tokens: int | None,
        latency_ms: int,
        finish_reason: str,
        ok: bool,
        output_chars: int | None,
        started_at: datetime,
    ) -> int | None:
        if self._store is None:
            return None
        return await self._store.fetchval(
            """INSERT INTO model_calls
               (stage, platform, conversation_id, model, reasoning, prompt_sha,
                prompt_chars, prompt_tokens, completion_tokens, reasoning_tokens,
                latency_ms, finish_reason, ok, output_chars, started_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
               RETURNING id""",
            stage,
            platform,
            conversation_id,
            self._model,
            reasoning,
            prompt_sha,
            prompt_chars,
            prompt_tokens,
            completion_tokens,
            reasoning_tokens,
            latency_ms,
            finish_reason,
            ok,
            output_chars,
            started_at,
        )
