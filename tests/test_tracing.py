"""Tracing must be invisible when it fails.

The property under test is not "traces arrive" — that needs Langfuse — but
"nothing about tracing can stop the agent", plus the one thing that must
never reach a third party: a real phone number.
"""

from __future__ import annotations

import pytest

from loop import tracing


@pytest.mark.parametrize(
    "raw",
    [
        "call me on +85212345678",
        "85297756885 is my number",
        "+852 1234 5678",
        "reach me at +852-1234-5678",
        "852.1234.5678",
    ],
)
def test_phone_numbers_are_redacted(raw: str) -> None:
    masked = tracing.redact_phone_numbers(raw)
    assert "[phone]" in masked
    for run in ("85212345678", "85297756885", "1234 5678", "1234-5678", "1234.5678"):
        assert run not in masked


def test_ordinary_text_survives() -> None:
    """A transcript that is not a phone number must still be readable — the
    whole point of tracing is seeing what the model saw."""
    raw = "聽日幾點去 Cyberport？ lets meet at 10"
    assert tracing.redact_phone_numbers(raw) == raw


def test_mask_walks_nested_structures() -> None:
    """A chat line reaches Langfuse nested inside the OpenAI request body,
    not as a top-level string — masking only the top level would miss every
    real message."""
    payload = {
        "messages": [
            {"role": "user", "content": "ring +85212345678 later"},
            {"role": "system", "content": "no numbers here"},
        ]
    }
    masked = tracing._mask(payload)
    assert "[phone]" in masked["messages"][0]["content"]
    assert masked["messages"][1]["content"] == "no numbers here"


def test_mask_never_raises_on_odd_input() -> None:
    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("no")

    for value in (None, 3, 4.5, b"bytes", Hostile()):
        tracing._mask(value)  # must not raise


def test_init_returns_none_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No keys, no tracing, no error — the agent runs identically with
    Langfuse switched off."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.is_configured() is False
    assert tracing.init() is None


def test_flush_of_none_is_a_noop() -> None:
    tracing.flush(None)


def test_flush_swallows_a_failing_client() -> None:
    class Exploding:
        def flush(self) -> None:
            raise RuntimeError("langfuse down")

    tracing.flush(Exploding())  # must not raise


def test_traced_openai_returns_a_client_without_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller gets the same object either way, so nothing downstream
    knows whether tracing is on."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    client = tracing.traced_openai(base_url="https://example.invalid/v1", api_key="x")
    assert hasattr(client, "chat")
