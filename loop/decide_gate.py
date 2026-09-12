"""The Relevance Gate: transcript + Standing -> `go` / `no_go`, fail-closed.

The cheap first judgement in front of the expensive participation turn
(`design/01 §"Orient -> Decide"`, `design/07` item 70). It sees exactly two
render blocks — `render_transcript` (with ages) and `render_standing` — never
notes, never `<loop_decisions>`, never her memory: that narrower input is
`prompts/gate.md`'s own contract, not this module's choice to relax.

**Fail-closed is the whole point.** Every one of these degrades to
`verdict="no_go"` with `error_kind` set on the `gate_log` row, never an
exception into the caller (the same convention `loop/search.py`,
`loop/route.py`, `loop/memory.py` and `loop/vision.py` hold):

- a timeout (surfaced by `ModelClient` as `ok=False, error_kind="timeout"`)
- any other transport/API failure `ModelClient` did not itself catch (it only
  catches `TimeoutError` around its `wait_for`; anything else — a connection
  error, a malformed fake in a test — is caught here instead, at the one
  point that MUST NOT let it through)
- a `finish_reason` that is not `"stop"` (`ModelClient` already reduces this
  to `ok=False, error_kind=finish_reason`)
- a body that is not parseable JSON, not a JSON object, or has no string
  `verdict` field
- a `verdict` string outside the prompt's own closed vocabulary (`go` /
  `no_go`)

A genuine `no_go` verdict from the model is not an error: `error_kind` stays
`None` on both a `go` and an honest `no_go` — silence is the expected,
healthy outcome (`prompts/gate.md`, "Silence is the expected answer"), not a
failure mode.

**One `gate_log` row per judged window, always** — success or fail-closed —
carrying `completion.call_id` so a wrong (or missing) verdict is one join
away from its `model_calls` row (ORA-17). The gate never writes a
`decisions` row (DEC-179): `gate_log` is observability only.

`prompts/gate.md`'s JSON contract names two fields in its own text —
`verdict` and `relevance_score` — but the file never shows a literal
example object. This module reads those two names literally as the JSON
keys; `relevance_score` is read leniently (any JSON number, not just the
three named values 0.0/0.5/1.0) since the file calls it a confidence
signal that "never decides anything by itself" — a stray fourth value
should not itself fail the window closed when `verdict` is well-formed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loop.gate_log import record as _record_gate_log
from loop.model import ModelClient
from loop.render import StandingEntry, TranscriptRow, render_standing, render_transcript
from loop.store import Store

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "gate.md"

# design/03 §"model_timeout_seconds": "gate 8s" — the cheap call gets the
# short leash; ORA-14: "none" is the only reasoning_effort that exists for it.
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_REASONING_EFFORT = "none"

_VALID_VERDICTS = {"go", "no_go"}


@dataclass(frozen=True)
class GateResult:
    """`verdict` is always `"go"` or `"no_go"` — a failure never leaves it
    unset or invents a third value. `error_kind` is `None` exactly when the
    model actually produced this verdict; anything else means the window
    was judged closed rather than judged at all."""

    verdict: str
    score: float | None
    error_kind: str | None
    call_id: int | None
    gate_log_id: int


def load_system_prompt(path: Path = PROMPT_PATH) -> str:
    """`prompts/gate.md`'s instruction body — everything after the closing
    `---` of its YAML frontmatter. Never edited (this file is Nora's
    measured instruction); this just knows how to strip the header."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


def _parse_verdict(content: str) -> tuple[str, float | None] | None:
    """`None` on anything that is not a JSON object carrying a string
    `verdict` — the caller turns that into `error_kind="unparseable_json"`.
    A verdict outside the closed vocabulary is returned as-is; the caller
    is the one that judges it against `_VALID_VERDICTS`, so that failure
    gets its own `error_kind` rather than being folded into this one."""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    verdict = parsed.get("verdict")
    if not isinstance(verdict, str):
        return None
    score_raw = parsed.get("relevance_score")
    score = float(score_raw) if isinstance(score_raw, int | float) and not isinstance(
        score_raw, bool
    ) else None
    return verdict, score


async def judge(
    model: ModelClient,
    store: Store,
    *,
    platform: str,
    conversation_id: str,
    rows: list[TranscriptRow],
    standing: StandingEntry | None,
    window_rows: int,
    window_end_row_id: int,
    now: datetime,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> GateResult:
    """One judged window in, one `GateResult` out, one `gate_log` row
    written either way. Never raises (rule 2): a transport failure
    `ModelClient` did not itself catch is caught here, at the last point
    before it would otherwise reach the loop."""
    system = load_system_prompt()
    transcript_block = render_transcript(rows, now=now)
    standing_block = render_standing(standing, now=now)
    prompt = "\n\n".join(b for b in (transcript_block, standing_block) if b)

    try:
        completion = await model.complete_json(
            stage="gate",
            system=system,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
            platform=platform,
            conversation_id=conversation_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (rule 2)
        return await _fail_closed(
            store,
            platform=platform,
            conversation_id=conversation_id,
            window_rows=window_rows,
            window_end_row_id=window_end_row_id,
            error_kind=type(exc).__name__,
            call_id=None,
            now=now,
        )

    if not completion.ok:
        return await _fail_closed(
            store,
            platform=platform,
            conversation_id=conversation_id,
            window_rows=window_rows,
            window_end_row_id=window_end_row_id,
            error_kind=completion.error_kind or "error",
            call_id=completion.call_id,
            now=now,
        )

    parsed = _parse_verdict(completion.content)
    if parsed is None:
        return await _fail_closed(
            store,
            platform=platform,
            conversation_id=conversation_id,
            window_rows=window_rows,
            window_end_row_id=window_end_row_id,
            error_kind="unparseable_json",
            call_id=completion.call_id,
            now=now,
        )

    verdict, score = parsed
    if verdict not in _VALID_VERDICTS:
        return await _fail_closed(
            store,
            platform=platform,
            conversation_id=conversation_id,
            window_rows=window_rows,
            window_end_row_id=window_end_row_id,
            error_kind="unknown_verdict",
            call_id=completion.call_id,
            now=now,
        )

    gate_log_id = await _record_gate_log(
        store,
        platform=platform,
        conversation_id=conversation_id,
        verdict=verdict,
        window_rows=window_rows,
        window_end_row_id=window_end_row_id,
        score=score,
        error_kind=None,
        call_id=completion.call_id,
        ts=now,
    )
    return GateResult(
        verdict=verdict, score=score, error_kind=None,
        call_id=completion.call_id, gate_log_id=gate_log_id,
    )


async def _fail_closed(
    store: Store,
    *,
    platform: str,
    conversation_id: str,
    window_rows: int,
    window_end_row_id: int,
    error_kind: str,
    call_id: int | None,
    now: datetime,
) -> GateResult:
    """The one path every failure mode routes through: a `no_go` verdict,
    the `error_kind` that earned it, and the `gate_log` row that makes the
    fail-closed decision itself observable — a silent gate and a broken
    gate must not look the same from `loop_tail`."""
    gate_log_id = await _record_gate_log(
        store,
        platform=platform,
        conversation_id=conversation_id,
        verdict="no_go",
        window_rows=window_rows,
        window_end_row_id=window_end_row_id,
        score=None,
        error_kind=error_kind,
        call_id=call_id,
        ts=now,
    )
    return GateResult(
        verdict="no_go", score=None, error_kind=error_kind,
        call_id=call_id, gate_log_id=gate_log_id,
    )
