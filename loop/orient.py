"""Orient: Ora's memory. Observe writes raw `messages` rows; Orient turns
them into the two things every later judgement reads — the standing fold
and the note index — and tidies the index behind them. Three jobs, three
entry points, one model call each:

- `fold()` — rewrites ONE room's `standing` paragraph from the messages
  folded since its watermark (`prompts/fold.md`).
- `propose_notes()` — reads a batch of HUMAN messages and proposes new
  `note` rows for whatever the room left open (`prompts/notes.md`).
- `curate()` — walks open notes and retires the ones that are done:
  `closed` (cited), `expired`, or `moot` (`prompts/curation.md`).

Every failure mode in every job changes nothing and never raises — the
same discipline `loop/decide_gate.py`, `loop/search.py`, `loop/route.py`,
`loop/memory.py` and `loop/vision.py` hold: a timeout, a transport error
`ModelClient` did not itself catch, a non-`stop` finish, or unparseable /
schema-invalid JSON all degrade to a no-op with `error_kind` set, never an
exception into the loop.

Two deliberate design choices, both because the callers this module will
get (`loop.py`, event-day) already hold the state these jobs need:

- Like `decide_gate.judge()`, every function takes its input **already
  fetched** — the prior `standing` row, the message rows to fold or read,
  the open notes and their evidence — rather than querying `messages` or
  `note` itself. Orient owns the WRITE side of `standing` and `note` (it
  calls `store.execute` for both) but not the read side; that keeps
  `MessageRow`'s shape (a resolved sender label, not a raw `sender_id`)
  someone else's join to make, once, before Orient ever sees it (CMS:
  comprehend once).
- `TranscriptRow` (`loop/render.py`) carries no row id, so `render_transcript`
  cannot produce the numbered batch `prompts/notes.md` and `prompts/curation.md`
  both require (a per-message `id` to verdict against; "the number in
  square brackets" to cite). `_render_message_batch` below is the one
  place in this module that composes prompt text itself instead of going
  through `render.py` — it still reuses `render.age` / `render.escape` for
  everything it can, and every other block (`<standing>`, `<notes>`) is
  built through `render.py` as instructed.

Ambiguities in the three prompts, and what this module chose:

- `prompts/fold.md` is Nora's per-turn debrief prompt verbatim (`<standing_prior>`,
  `<transcript>`, `<reasoning>` for ONE finished exchange) but Orient folds
  a BATCH of raw messages since a watermark, with no decider `<reasoning>`
  to show it — Orient has none to give. This module renders `<standing>`
  and `<transcript>` only and omits `<reasoning>` entirely, rather than
  fabricating one; the prompt's own text already tolerates a turn "where I
  deliberately said nothing".
- `fold.md`'s own JSON contract also carries a `"notes"` array (its own
  quite different shape: title/description/uncertainty/closing_condition/
  anchor_date/body). This module reads `"standing"` only and ignores it —
  note creation is `propose_notes()`'s job, over `prompts/notes.md`'s own
  distinct schema; reading the same kind of thing through two different
  contracts in one fold is how two copies drift (CMS: interpret once).
- `prompts/notes.md`'s candidate object carries `description`, `body`,
  `anchor_entity_ids` and `message_ids` fields the `note` table (as this
  module owns it) has no column for. Only `title`, `closing_condition` and
  `anchor_at` (parsed from `anchor_date`) survive into the stored row; the
  rest is dropped rather than smuggled into a column it was not meant for
  (CMS serialize: an explicit drop, not a hidden one). `anchor_place` has
  no source in `notes.md` at all and is always written `NULL`.
  `message_ids` is still enforced as a *validity* check (a candidate
  naming an id outside the batch is discarded whole, per the prompt's own
  words) even though the ids themselves are not persisted.
- `prompts/curation.md`'s `discharge` array (`commitment` / `person_claim`)
  has no table to land in within this module's schema (`03 §"no verdict in
  v1"`); it is read for validation only and then dropped, same reasoning
  as above.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loop.model import ModelClient
from loop.render import (
    NoteEntry,
    StandingEntry,
    TranscriptRow,
    age,
    escape,
    escape_attr,
    render_notes,
    render_standing,
    render_transcript,
)
from loop.store import Store

FOLD_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "fold.md"
NOTES_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "notes.md"
CURATION_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "curation.md"

DEFAULT_REASONING_EFFORT = "low"  # ORA-14: "none" or "low" only, pinned per call
DEFAULT_TIMEOUT_SECONDS = 20.0
STANDING_MAX_CHARS = 1200  # rule 4: enforced here, never trusted from the model

_VALID_RETIRE_REASONS = {"closed", "expired", "moot"}


def _load_system_prompt(path: Path) -> str:
    """Everything after the closing `---` of a prompt's YAML frontmatter.
    Never edited — these are measured instructions; this just knows how to
    strip the header (same helper as `loop/decide_gate.py::load_system_prompt`)."""
    text = path.read_text()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


@dataclass(frozen=True)
class MessageRow:
    """One `messages` row, comprehended once into exactly what Orient needs
    — a resolved `sender_label`, not the raw `sender_id`. `id` is the real
    Postgres row id: the watermark advances over it, `notes.md`'s verdicts
    key on it, and a citation cites it."""

    id: int
    sender_label: str
    body: str
    ts: datetime
    is_ora: bool = False

    def as_transcript_row(self) -> TranscriptRow:
        return TranscriptRow(
            sender_label=self.sender_label, body=self.body, ts=self.ts, is_ora=self.is_ora
        )


@dataclass(frozen=True)
class StandingState:
    """The `standing` row exactly as `fold()` needs it, read once by the
    caller before this call — Orient never re-queries it mid-fold."""

    body: str = ""
    body_len: int = 0
    fold_count: int = 0
    last_folded_row_id: int | None = None
    last_call_id: int | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FoldResult:
    """`changed=False` means the `standing` row in Postgres was not
    touched — every field below then equals `prior`'s, so a failed fold is
    provably indistinguishable, from the stored row's point of view, from
    no fold happening at all (rule 5). `call_id` is the `model_calls` row
    for THIS attempt if one was made, whether or not it succeeded — kept
    separately from `last_call_id` (what `standing.last_call_id` actually
    holds) so a failure's own trace is never lost even though nothing was
    persisted."""

    changed: bool
    body: str
    body_len: int
    fold_count: int
    last_folded_row_id: int | None
    last_call_id: int | None
    updated_at: datetime | None
    call_id: int | None = None
    error_kind: str | None = None


@dataclass(frozen=True)
class CreatedNote:
    id: str
    title: str
    closing_condition: str
    anchor_at: datetime | None
    created_call_id: int | None


@dataclass(frozen=True)
class NotesResult:
    created: list[CreatedNote] = field(default_factory=list)
    call_id: int | None = None
    error_kind: str | None = None


@dataclass(frozen=True)
class NoteForCuration:
    """One open note plus its evidence — the messages said since it was
    written, oldest first, possibly empty. Orient does not query for this;
    the caller (who owns `note` and `messages` reads) hands it over."""

    id: str
    title: str
    closing_condition: str
    anchor_at: datetime | None
    evidence: list[MessageRow] = field(default_factory=list)


@dataclass(frozen=True)
class RetiredNote:
    id: str
    reason: str  # 'closed' | 'expired' | 'moot'
    cited_row_id: int | None


@dataclass(frozen=True)
class CurationResult:
    retired: list[RetiredNote] = field(default_factory=list)
    call_id: int | None = None
    error_kind: str | None = None


def _render_message_batch(rows: list[MessageRow], *, now: datetime, tag: str) -> str:
    """A numbered batch — `[id] (age) sender: body` — for the two prompts
    that must cite a message by its integer id. `render_transcript` cannot
    do this (`TranscriptRow` carries no id); this is the one hand-rolled
    block in this module, and it still reuses `render.age` / `render.escape`."""
    if not rows:
        return f'<{tag} untrusted="true">(none)</{tag}>'
    lines = [
        f"[{r.id}] ({age(r.ts, now)}) {escape(r.sender_label)}: {escape(r.body)}" for r in rows
    ]
    return f'<{tag} untrusted="true">\n' + "\n".join(lines) + f"\n</{tag}>"


def _render_note_for_curation(note: NoteForCuration, *, now: datetime) -> str:
    if note.anchor_at is None:
        anchor = "no anchor date set"
    else:
        passed = "has passed" if now > note.anchor_at else "has not passed"
        anchor = f"{note.anchor_at.date().isoformat()} ({passed})"
    evidence = _render_message_batch(note.evidence, now=now, tag="evidence")
    return (
        f'<note id="{escape_attr(note.id)}" title="{escape_attr(note.title)}" '
        f'closing_condition="{escape_attr(note.closing_condition)}" '
        f'anchor="{escape_attr(anchor)}">\n{evidence}\n</note>'
    )


def _parse_fold(content: str) -> str | None:
    """`None` on anything that is not a JSON object carrying a non-blank
    string `standing` — the caller turns that into `error_kind`. The
    contract's own `"notes"` array is deliberately not read here (see the
    module docstring's ambiguity note)."""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    standing = parsed.get("standing")
    if not isinstance(standing, str) or not standing.strip():
        return None
    return standing.strip()


def _parse_note_candidates(content: str) -> list[Any] | None:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    candidates = parsed.get("candidates")
    if not isinstance(candidates, list):
        return None
    return candidates


def _parse_curation_verdicts(content: str) -> list[Any] | None:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    verdicts = parsed.get("verdicts")
    if not isinstance(verdicts, list):
        return None
    return verdicts


def _parse_anchor_date(value: object) -> datetime | None:
    """`notes.md`'s `anchor_date` is an eight-digit `YYYYMMDD` int.
    Anything else — missing, a string, out of range — degrades to no
    anchor rather than failing the whole candidate; `anchor_at` is
    nullable exactly for this."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return None


async def fold(
    model: ModelClient,
    store: Store,
    *,
    platform: str,
    conversation_id: str,
    rows: list[MessageRow],
    prior: StandingState,
    now: datetime,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> FoldResult:
    """Rewrite the standing paragraph from `rows` (the messages folded
    since `prior.last_folded_row_id` — selecting that window is the
    caller's job, same as `decide_gate.judge()`'s `rows` param).

    The watermark rule (rule 2): advances to `max(r.id for r in rows)` —
    exactly the rows this call actually folded — and only on a persisted
    success. `rows == []` is a no-op: nothing new to fold, so no model
    call, no write, watermark untouched (folding twice over the same rows
    is exactly what an eager watermark would cause).
    """
    unchanged = FoldResult(
        changed=False,
        body=prior.body,
        body_len=prior.body_len,
        fold_count=prior.fold_count,
        last_folded_row_id=prior.last_folded_row_id,
        last_call_id=prior.last_call_id,
        updated_at=prior.updated_at,
    )
    if not rows:
        return unchanged

    system = _load_system_prompt(FOLD_PROMPT_PATH)
    standing_entry = StandingEntry(body=prior.body, updated_at=prior.updated_at or now)
    blocks = [
        b
        for b in (
            render_standing(standing_entry, now=now),
            render_transcript([r.as_transcript_row() for r in rows], now=now),
        )
        if b
    ]
    prompt = "\n\n".join(blocks)

    try:
        completion = await model.complete_json(
            stage="fold",
            system=system,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
            platform=platform,
            conversation_id=conversation_id,
            # 1200 chars of standing is ~300-400 tokens before the JSON
            # wrapper; the 400 default truncated the first live fold and
            # came back finish_reason=length, which fails closed and so
            # silently never updates the position.
            max_tokens=4000,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (rule 5)
        return replace(unchanged, error_kind=type(exc).__name__)

    if not completion.ok:
        return replace(
            unchanged, call_id=completion.call_id, error_kind=completion.error_kind or "error"
        )

    standing_text = _parse_fold(completion.content)
    if standing_text is None:
        return replace(unchanged, call_id=completion.call_id, error_kind="unparseable_json")

    body = standing_text[:STANDING_MAX_CHARS]  # rule 4: enforced, not trusted
    body_len = len(body)
    fold_count = prior.fold_count + 1
    last_folded_row_id = max(r.id for r in rows)

    await store.execute(
        """INSERT INTO standing
           (platform, conversation_id, body, body_len, fold_count,
            last_folded_row_id, last_call_id, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (platform, conversation_id) DO UPDATE SET
             body = EXCLUDED.body, body_len = EXCLUDED.body_len,
             fold_count = EXCLUDED.fold_count,
             last_folded_row_id = EXCLUDED.last_folded_row_id,
             last_call_id = EXCLUDED.last_call_id,
             updated_at = EXCLUDED.updated_at""",
        platform,
        conversation_id,
        body,
        body_len,
        fold_count,
        last_folded_row_id,
        completion.call_id,
        now,
    )

    return FoldResult(
        changed=True,
        body=body,
        body_len=body_len,
        fold_count=fold_count,
        last_folded_row_id=last_folded_row_id,
        last_call_id=completion.call_id,
        updated_at=now,
        call_id=completion.call_id,
        error_kind=None,
    )


async def propose_notes(
    model: ModelClient,
    store: Store,
    *,
    workspace: str,
    platform: str,
    conversation_id: str,
    room_label: str,
    rows: list[MessageRow],
    open_notes: list[NoteEntry],
    now: datetime,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> NotesResult:
    """Propose new `note` rows from the HUMAN messages in `rows` (rule 1:
    the filter runs BEFORE the prompt is built, not after the model
    answers — an `is_ora` row never even reaches the model, so there is
    nothing for it to invent a note out of). `open_notes` is the notebook
    index the model is shown so it does not propose a duplicate.
    """
    human_rows = [r for r in rows if not r.is_ora]
    if not human_rows:
        return NotesResult(created=[])

    system = _load_system_prompt(NOTES_PROMPT_PATH)
    blocks = [
        b
        for b in (
            render_notes(open_notes, now=now),
            _render_message_batch(human_rows, now=now, tag="messages"),
        )
        if b
    ]
    prompt = "\n\n".join(blocks)

    try:
        completion = await model.complete_json(
            stage="notes",
            system=system,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
            platform=platform,
            conversation_id=conversation_id,
            max_tokens=4000,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (rule 5)
        return NotesResult(created=[], error_kind=type(exc).__name__)

    if not completion.ok:
        return NotesResult(
            created=[], call_id=completion.call_id, error_kind=completion.error_kind or "error"
        )

    candidates = _parse_note_candidates(completion.content)
    if candidates is None:
        return NotesResult(created=[], call_id=completion.call_id, error_kind="unparseable_json")

    batch_ids = {r.id for r in human_rows}
    created: list[CreatedNote] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        title = candidate.get("title")
        closing_condition = candidate.get("closing_condition")
        message_ids = candidate.get("message_ids")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(closing_condition, str) or not closing_condition.strip():
            continue
        # notes.md, verbatim: "an id outside the batch discards the whole proposal".
        if not isinstance(message_ids, list) or not message_ids:
            continue
        ids_ok = all(
            isinstance(m, int) and not isinstance(m, bool) and m in batch_ids for m in message_ids
        )
        if not ids_ok:
            continue

        note_id = secrets.token_hex(4)  # 8-hex, per schema
        anchor_at = _parse_anchor_date(candidate.get("anchor_date"))
        title_clean = title.strip()
        closing_condition_clean = closing_condition.strip()

        await store.execute(
            """INSERT INTO note
               (id, workspace, room_label, title, closing_condition,
                anchor_at, anchor_place, created_at, created_call_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            note_id,
            workspace,
            room_label,
            title_clean,
            closing_condition_clean,
            anchor_at,
            None,  # anchor_place: notes.md has no source for this (see module docstring)
            now,
            completion.call_id,
        )
        created.append(
            CreatedNote(
                id=note_id,
                title=title_clean,
                closing_condition=closing_condition_clean,
                anchor_at=anchor_at,
                created_call_id=completion.call_id,
            )
        )

    return NotesResult(created=created, call_id=completion.call_id, error_kind=None)


async def curate(
    model: ModelClient,
    store: Store,
    *,
    notes: list[NoteForCuration],
    now: datetime,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CurationResult:
    """Walk `notes` and retire the ones that are done. Rule 3: a `closed`
    verdict is only honoured when `cited_message_id` is an int that
    actually appears in THAT note's own evidence — anything else (missing,
    wrong type, or citing a row outside this note's evidence) is refused
    and the note is left open, exactly as `curation.md` itself demands
    ("the entry will be refused"). `expired` and `moot` need no citation.
    """
    if not notes:
        return CurationResult(retired=[])

    system = _load_system_prompt(CURATION_PROMPT_PATH)
    prompt = "\n\n".join(_render_note_for_curation(n, now=now) for n in notes)

    try:
        completion = await model.complete_json(
            stage="curation",
            system=system,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
            max_tokens=4000,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — never raises into the loop (rule 5)
        return CurationResult(retired=[], error_kind=type(exc).__name__)

    if not completion.ok:
        return CurationResult(
            retired=[], call_id=completion.call_id, error_kind=completion.error_kind or "error"
        )

    verdicts = _parse_curation_verdicts(completion.content)
    if verdicts is None:
        return CurationResult(retired=[], call_id=completion.call_id, error_kind="unparseable_json")

    by_id = {n.id: n for n in notes}
    retired: list[RetiredNote] = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        note_id = v.get("note_id")
        verdict = v.get("verdict")
        if not isinstance(note_id, str) or note_id not in by_id:
            continue
        if verdict == "open":
            continue  # the ordinary, healthy answer — nothing to retire
        if verdict not in _VALID_RETIRE_REASONS:
            continue

        note = by_id[note_id]
        cited_row_id: int | None = None
        if verdict == "closed":
            cited = v.get("cited_message_id")
            evidence_ids = {r.id for r in note.evidence}
            if not isinstance(cited, int) or isinstance(cited, bool) or cited not in evidence_ids:
                continue  # rule 3: a close with no real citation is not a close
            cited_row_id = cited

        await store.execute(
            """UPDATE note SET retired_at = $1, retired_reason = $2, cited_row_id = $3
               WHERE id = $4 AND retired_at IS NULL""",
            now,
            verdict,
            cited_row_id,
            note_id,
        )
        retired.append(RetiredNote(id=note_id, reason=verdict, cited_row_id=cited_row_id))

    return CurationResult(retired=retired, call_id=completion.call_id, error_kind=None)
