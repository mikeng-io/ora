"""The runtime: the thing that actually runs.

Everything else in `loop/` is a part that can be tested in isolation. This
is the wiring — the only module that knows all of them exist, and the only
one with a `while True` in it.

Three concurrent jobs, one per reason Ora might speak:

- the **observers** write rows as they arrive and never decide anything;
- the **room loop**, one per listed room, watches for rows that have gone
  quiet and runs gate -> turn over the window;
- the **proactive tick** runs on a timer over the whole workspace, which is
  the one path that can speak with nobody having said anything at all.

The tag path is not a fourth job: a tagged row is noticed by the room loop
the moment it lands and short-circuits the gate, because being called is
already the answer to «is this worth a turn».

Why polling rather than a queue: the observers commit rows before anything
reads them, so the store is already the handoff. A queue would be a second
copy of the truth, and the two would drift under a restart — the loop is
supposed to survive one and pick up from the rows.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loop import (
    act,
    decide_gate,
    decide_proactive,
    decide_turn,
    orient,
    present,
    recall,
    tag,
    tracing,
    windows,
)
from loop import (
    route as route_mod,
)
from loop.config import Config, Room, load_config
from loop.logging import LogSink
from loop.memory import HonchoClient
from loop.model import ModelClient
from loop.observe_signal import SignalObserver
from loop.observe_whatsapp import WhatsAppObserver
from loop.people import PeopleDirectory, load_people
from loop.render import (
    DecisionEntry,
    NoteEntry,
    StandingEntry,
    TranscriptRow,
    join_media_into_body,
)
from loop.search import ExaSearchProvider
from loop.store import Store

TAG_PATTERN = re.compile(r"@ora\b", re.IGNORECASE)


def is_tagged(body: str, ora_ids: tuple[str, ...] = ()) -> bool:
    """A literal `@ora`, or a platform mention of one of Ora's own ids.

    Deliberately not a name match on "ora" alone: a room that says «ora»
    in passing has not called anybody, and a turn spent on that is a turn
    the room did not ask for.

    `ora_ids` is not optional in practice, it only looks it. Tapping a name
    in the Signal or WhatsApp mention UI — the most natural way anyone
    actually calls her — does NOT produce the text "@ora": WhatsApp sends
    `@137259286286429`, the raw LID. Measured live: a real tag arrived as
    «@137259286286429 test» and did not fire this path at all, because
    nothing was passing the ids. The agent looked ignorant of a direct
    address, and nothing in the log said why.
    """
    if TAG_PATTERN.search(body):
        return True
    return any(i and i in body for i in ora_ids)


@dataclass
class RoomState:
    """What the loop remembers between passes for one room. Deliberately
    small: anything that must survive a restart lives in Postgres, not
    here."""

    last_seen_row_id: int = 0
    last_spoke_at: datetime | None = None
    folded_through: int = 0
    pending_since: datetime | None = None
    pending_rows: list[int] = field(default_factory=list)


async def _rows_since(
    store: Store, platform: str, conversation_id: str, after_id: int, limit: int = 50
) -> list[Any]:
    return await store.fetch(
        """SELECT id, sender_id, person_id, is_ora, ts, body, media_sha256,
                  COALESCE(is_reply_to_ora, FALSE) AS is_reply_to_ora, platform_message_id
             FROM messages
            WHERE platform = $1 AND conversation_id = $2 AND id > $3
            ORDER BY id ASC
            LIMIT $4""",
        platform,
        conversation_id,
        after_id,
        limit,
    )


async def _transcript(
    store: Store, room: Room, people: PeopleDirectory, *, limit: int = 20
) -> list[TranscriptRow]:
    """The window a decider reads, newest `limit` rows, oldest first.

    The media join happens HERE and nowhere else: `messages.body` keeps the
    bare `[image abc123]` placeholder it was written with, and the
    comprehended title/description is folded in for this one render. The
    stored row stays the audit trail; the model gets the readable version.
    """
    rows = await store.fetch(
        """SELECT m.id, m.sender_id, m.person_id, m.is_ora, m.ts, m.body,
                  m.media_sha256, o.title, o.description
             FROM messages m
        LEFT JOIN media_objects o ON o.sha256 = m.media_sha256
            WHERE m.platform = $1 AND m.conversation_id = $2
            ORDER BY m.id DESC
            LIMIT $3""",
        room.platform,
        room.conversation_id,
        limit,
    )
    out: list[TranscriptRow] = []
    for r in reversed(rows):
        body = r["body"]
        if r["media_sha256"]:
            body = join_media_into_body(
                body, r["media_sha256"][:6], r["title"] or "", r["description"] or ""
            )
        person = people.resolve(room.platform, r["sender_id"]) if r["sender_id"] else None
        label = "Ora" if r["is_ora"] else (person.name if person else (r["sender_id"] or "someone"))
        out.append(TranscriptRow(sender_label=label, body=body, ts=r["ts"], is_ora=r["is_ora"]))
    return out


async def _standing(store: Store, room: Room) -> StandingEntry | None:
    row = await store.fetchrow(
        "SELECT body, updated_at FROM standing WHERE platform = $1 AND conversation_id = $2",
        room.platform,
        room.conversation_id,
    )
    if row is None or not row["body"]:
        return None
    return StandingEntry(body=row["body"], updated_at=row["updated_at"])


async def _open_notes(store: Store, workspace: str) -> list[Any]:
    return await store.fetch(
        """SELECT id, title, closing_condition, created_at, room_label, strikes, revisit_at
             FROM note
            WHERE workspace = $1 AND retired_at IS NULL
            ORDER BY created_at ASC""",
        workspace,
    )


def _note_entries(rows: list[Any]) -> list[NoteEntry]:
    return [
        NoteEntry(
            id=r["id"],
            title=r["title"],
            closing_condition=r["closing_condition"],
            created_at=r["created_at"],
            room_label=r["room_label"],
        )
        for r in rows
    ]


async def _loop_decisions(
    store: Store, workspace: str, writers: tuple[str, ...], *, limit: int = 20
) -> list[DecisionEntry]:
    from loop import decisions as decisions_mod

    rows = await decisions_mod.recent_for(store, workspace, writers, limit=limit)
    return [
        DecisionEntry(
            writer=r["writer"],
            verdict=r["verdict"],
            room_label=r["conversation_id"][:8],
            ts=r["ts"],
            note_id=r["note_id"],
            reminder_id=r["reminder_id"],
        )
        for r in rows
    ]


class Loop:
    def __init__(
        self,
        config: Config,
        store: Store,
        sink: LogSink,
        people: PeopleDirectory,
        model: ModelClient,
        *,
        vision_client: Any | None = None,
        honcho: Any | None = None,
        search_provider: Any | None = None,
        presenter: Any | None = None,
        weather_fn: Any | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._sink = sink
        self._people = people
        self._model = model
        self._vision_client = vision_client
        self._state: dict[tuple[str, str], RoomState] = {}
        self._guard = windows.RoomGuard()
        self._honcho = honcho
        self._search = search_provider
        self._presenter = presenter
        self._weather = weather_fn
        self._ora_ids: tuple[str, ...] = ()
        self._fed_through = 0
        self._warned_no_home = False

    def _room_state(self, room: Room) -> RoomState:
        key = (room.platform, room.conversation_id)
        if key not in self._state:
            self._state[key] = RoomState()
        return self._state[key]

    # ---- one room -------------------------------------------------------

    async def run_room(self, room: Room) -> None:
        """Watch one room. A pass fires only after the room has been quiet
        for `settle_seconds` — a room mid-sentence is not a room that has
        finished saying something."""
        clocks = self._config.clocks
        state = self._room_state(room)
        state.last_seen_row_id = await self._newest_row_id(room)

        while True:
            try:
                await asyncio.sleep(2)
                rows = await _rows_since(
                    self._store, room.platform, room.conversation_id, state.last_seen_row_id
                )
                now = datetime.now(UTC)

                if rows:
                    state.last_seen_row_id = rows[-1]["id"]
                    state.pending_rows.extend(r["id"] for r in rows)
                    state.pending_since = now
                    await self._remember(room, rows)
                    # Replying to something Ora said IS addressing Ora — the
                    # reply path. People answer a message far more naturally
                    # than they re-type a name, so treating only @-mentions
                    # as being spoken to misses half of being spoken to.
                    tagged = next(
                        (
                            r
                            for r in rows
                            if not r["is_ora"]
                            and (
                                is_tagged(r["body"] or "", self._ora_ids)
                                or r["is_reply_to_ora"]
                            )
                        ),
                        None,
                    )
                    if tagged is not None:
                        # A tag always tries to speak — `tag.handle_tag` never
                        # chooses silence — so without a cooldown here, three
                        # quick "@ora" messages get three full replies fired
                        # back to back. That reads as frantic on stage and can
                        # trip platform rate limits, which then degrade real
                        # answers into delivery failures.
                        cooling = await windows.in_cooldown(
                            self._store, clocks,
                            platform=room.platform,
                            conversation_id=room.conversation_id, now=now,
                        )
                        if cooling.active:
                            self._sink.event(
                                "TAG", room.label,
                                f"held — cooldown ({int(cooling.remaining_seconds)}s left)",
                                platform=room.platform,
                            )
                        else:
                            async with self._guard.hold(
                                room.platform, room.conversation_id
                            ) as held:
                                if held:
                                    await self._react(room, tagged, "👀")
                                    ok = await self._run_tag(
                                        room, now, tagged["body"] or ""
                                    )
                                    await self._react(
                                        room, tagged, "✅" if ok else "⚠️"
                                    )
                        state.pending_since = None
                        state.pending_rows.clear()
                    continue

                if state.pending_since is None or not state.pending_rows:
                    continue
                quiet_for = (now - state.pending_since).total_seconds()
                if quiet_for < clocks.settle_seconds:
                    continue

                # Bounded to the NEWEST rows: a gate judges what is happening
                # now, and an unbounded burst would blow the turn's budget.
                window = windows.bound_window(
                    list(state.pending_rows), clocks.max_window_messages
                )
                state.pending_since = None
                state.pending_rows.clear()

                # Skip rather than queue: by the time a running pass finishes,
                # the window this one would judge is stale, and judging a stale
                # window is how an agent answers a conversation that moved on.
                async with self._guard.hold(room.platform, room.conversation_id) as held:
                    if held:
                        await self._run_window(room, window, now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a room must not kill the loop
                self._sink.event("GATE", room.label, f"room pass failed: {type(exc).__name__}",
                                 error=True, platform=room.platform)

    async def learn_own_ids(self) -> tuple[str, ...]:
        """Ask the platforms who Ora is, so a native mention is recognised.

        Asked at runtime rather than configured: the WhatsApp LID is issued
        by WhatsApp at pairing and is not something anyone can write into a
        config file in advance, and getting it wrong means every tap of
        Ora's name in the mention UI is silently ignored.
        """
        ids: list[str] = []
        account = self._config.env.signal_account
        if account:
            ids.append(account)
            ids.append(account.lstrip("+"))
        base = self._config.env.whatsapp_base_url
        if base:
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session, session.get(
                    base + "/self", timeout=8
                ) as response:
                    payload = await response.json()
                for key in ("lid", "phone"):
                    value = payload.get(key)
                    if value:
                        ids.append(str(value))
            except Exception as exc:  # noqa: BLE001 — a tag path that degrades, never a crash
                self._sink.event(
                    "OBSERVE", "-",
                    f"could not learn WhatsApp identity ({type(exc).__name__}); "
                    "native mentions will not tag",
                    error=True,
                )
        self._ora_ids = tuple(dict.fromkeys(i for i in ids if i))
        return self._ora_ids

    def _toolbox(self, room: Room) -> Any:
        """The same toolbox for every path that can speak.

        The model chooses from it; nothing here pre-decides. Built per room
        because `remember` and `remind` write against a specific
        conversation — a reminder with no room is a reminder nobody can
        receive.
        """
        from loop import toolcall
        from loop import weather as weather_mod

        return toolcall.Toolbox(
            route_fn=route_mod.route,
            route_api_key=self._config.env.google_map_api_key,
            default_origin=self._config.env.ora_default_origin,
            search_provider=self._search,
            weather_fn=self._weather or weather_mod.weather,
            honcho=self._honcho,
            conversation_id=room.conversation_id,
            store=self._store,
            workspace=self._config.workspace,
        )

    async def _react(self, room: Room, row: Any, emoji: str) -> None:
        """Put an emoji on the message being worked on.

        This is the only feedback a person gets between sending and being
        answered. Without it a tagged user watches a silent chat for several
        seconds and concludes the agent is broken — on stage that reads as a
        dead demo, which is why it is worth a round trip.

        Never raises and never blocks the reply: a reaction that fails costs
        nothing, an answer that fails costs the turn.
        """
        message_id = row.get("platform_message_id") if hasattr(row, "get") else None
        if not message_id:
            return
        with contextlib.suppress(Exception):
            await act.react(
                self._config,
                room.platform,
                room.conversation_id,
                emoji,
                platform_message_id=str(message_id),
                author=row["sender_id"],
                store=self._store,
            )

    async def _show(self, text: str, room: Room) -> None:
        """Put a line on Ora's face.

        Called only where Ora actually spoke, so the avatar animates exactly
        when a decision to talk was made — that is the whole point of having
        a face on stage. Never raises and never blocks: a browser page that
        is closed, slow, or was never opened must not affect a real send that
        already happened.
        """
        if self._presenter is None or not text:
            return
        with contextlib.suppress(Exception):
            await self._presenter.speak(text, room=room.label, platform=room.platform)

    async def _remember(self, room: Room, rows: list[Any]) -> None:
        """Feed new rows to Honcho. Memory is written at Observe; this is
        that write. Failure is logged nowhere and costs nothing — a memory
        that is down must not stop the room from being read."""
        if self._honcho is None:
            return
        for r in rows:
            person = self._people.resolve(room.platform, r["sender_id"]) if r["sender_id"] else None
            label = "Ora" if r["is_ora"] else (person.name if person else "someone")
            await recall.remember(
                self._honcho,
                platform=room.platform,
                conversation_id=room.conversation_id,
                sender_label=label,
                body=r["body"] or "",
                is_ora=r["is_ora"],
                ts=r["ts"],
            )

    async def _recall(self, transcript: list[TranscriptRow]) -> recall.Recalled:
        """Read memory back for a turn. Degrades to empty, never to wrong:
        an empty card block simply does not render, while a fabricated one
        would put invented history in front of a decider."""
        if self._honcho is None:
            return recall.Recalled(self_conclusions=[], peer_facts=[])
        speakers = list({r.sender_label for r in transcript if not r.is_ora})
        return await recall.recall_for_turn(self._honcho, present=speakers)

    async def _newest_row_id(self, room: Room) -> int:
        row = await self._store.fetchval(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE platform=$1 AND conversation_id=$2",
            room.platform,
            room.conversation_id,
        )
        return row or 0

    async def _run_window(self, room: Room, window: list[int], now: datetime) -> None:
        """gate -> turn over one settled window, then fold."""
        state = self._room_state(room)
        clocks = self._config.clocks

        # Derived from the is_ora rows, not from memory: an in-process
        # `last_spoke_at` is forgotten on restart, and a forgotten cooldown
        # means Ora speaks twice in a row, which on stage reads as broken.
        cooldown = await windows.in_cooldown(
            self._store, clocks,
            platform=room.platform, conversation_id=room.conversation_id, now=now,
        )
        if cooldown.active:
            why = "cooldown check failed" if cooldown.errored else (
                f"cooldown ({int(cooldown.remaining_seconds)}s left)"
            )
            self._sink.event("GATE", room.label, f"no_go — {why}",
                             platform=room.platform, error=cooldown.errored)
            return

        transcript = await _transcript(self._store, room, self._people)
        standing = await _standing(self._store, room)

        gate = await decide_gate.judge(
            self._model,
            self._store,
            platform=room.platform,
            conversation_id=room.conversation_id,
            rows=transcript,
            standing=standing,
            window_rows=len(window),
            window_end_row_id=window[-1],
            now=now,
            timeout_seconds=clocks.gate_timeout_seconds,
        )
        detail = gate.verdict if gate.error_kind is None else f"{gate.verdict} ({gate.error_kind})"
        self._sink.event("GATE", room.label, detail, platform=room.platform,
                         error=gate.error_kind is not None)
        if gate.verdict != "go":
            await self._fold(room, now)
            return

        notes = await _open_notes(self._store, self._config.workspace)
        recalled = await self._recall(transcript)
        # The ambient turn gets the same tools as the tag path. Without them
        # it can only answer from memory — and demo step 1 is exactly the
        # case where the group asks each other a question, nobody tags Ora,
        # and the useful answer needs the world, not the transcript.
        result = await decide_turn.decide(
            model=self._model,
            config=self._config,
            store=self._store,
            people=self._people,
            workspace=self._config.workspace,
            platform=room.platform,
            conversation_id=room.conversation_id,
            now=now,
            transcript=transcript,
            standing=standing,
            notes=_note_entries(notes),
            loop_decisions=await _loop_decisions(
                self._store, self._config.workspace, ("participation", "proactive_act", "tag")
            ),
            loop_decision_writers=("participation", "proactive_act", "tag"),
            self_card_conclusions=recalled.self_conclusions,
            peer_cards=recalled.peer_facts,
        )
        self._sink.event("TURN", room.label, result.verdict, platform=room.platform,
                         error=result.verdict == "failed")
        if result.verdict == "spoke":
            state.last_spoke_at = now
            await self._show(result.text or "", room)
        await self._fold(room, now)

    async def _run_tag(self, room: Room, now: datetime, tagged_text: str = "") -> bool:
        transcript = await _transcript(self._store, room, self._people)
        standing = await _standing(self._store, room)
        notes = await _open_notes(self._store, self._config.workspace)

        recalled = await self._recall(transcript)
        peer_person, peer_facts = (
            recalled.peer_facts[0] if recalled.peer_facts else ("", [])
        )

        result = await tag.handle_tag(
            config=self._config,
            store=self._store,
            model=self._model,
            people=self._people,
            platform=room.platform,
            conversation_id=room.conversation_id,
            now=now,
            transcript=transcript,
            standing=standing,
            notes=_note_entries(notes),
            toolbox=self._toolbox(room),
            self_card=recalled.self_conclusions,
            peer_card_person=peer_person,
            peer_card_facts=peer_facts,
        )
        self._sink.event("TAG", room.label, result.verdict, platform=room.platform,
                         error=result.verdict == "failed")
        if result.verdict == "replied":
            self._room_state(room).last_spoke_at = now
            await self._show(result.text or "", room)
        await self._fold(room, now)
        return result.verdict == "replied"

    async def _fold(self, room: Room, now: datetime) -> None:
        """Orient: fold the room's position forward, then look for notes.

        Runs after every pass, including a `no_go` one — a room Ora chose
        not to speak in still moved, and a position that only updates when
        she talks is a position about her, not about them.
        """
        prior_row = await self._store.fetchrow(
            """SELECT body, body_len, fold_count, last_folded_row_id, last_call_id, updated_at
                 FROM standing WHERE platform=$1 AND conversation_id=$2""",
            room.platform,
            room.conversation_id,
        )
        prior = orient.StandingState(
            body=prior_row["body"] if prior_row else "",
            fold_count=prior_row["fold_count"] if prior_row else 0,
            last_folded_row_id=(prior_row["last_folded_row_id"] if prior_row else 0) or 0,
        )
        rows = await _rows_since(
            self._store, room.platform, room.conversation_id, prior.last_folded_row_id
        )
        if not rows:
            return
        batch = [
            orient.MessageRow(
                id=r["id"],
                sender_label=(
                    "Ora"
                    if r["is_ora"]
                    else (
                        p.name
                        if (p := self._people.resolve(room.platform, r["sender_id"]))
                        else (r["sender_id"] or "someone")
                    )
                ),
                body=r["body"],
                ts=r["ts"],
                is_ora=r["is_ora"],
            )
            for r in rows
        ]
        fold = await orient.fold(
            self._model,
            self._store,
            platform=room.platform,
            conversation_id=room.conversation_id,
            rows=batch,
            prior=prior,
            now=now,
        )
        if fold.changed:
            self._sink.event("FOLD", room.label, f"standing rewritten ({fold.body_len} chars)",
                             platform=room.platform)

        open_rows = await _open_notes(self._store, self._config.workspace)
        notes = await orient.propose_notes(
            self._model,
            self._store,
            workspace=self._config.workspace,
            platform=room.platform,
            conversation_id=room.conversation_id,
            room_label=room.label,
            rows=batch,
            open_notes=_note_entries(open_rows),
            now=now,
        )
        for n in notes.created:
            self._sink.event("CURATION", room.label, f"note {n.id} raised",
                             platform=room.platform)

        await self._curate(room, now)

    async def _curate(self, room: Room, now: datetime) -> None:
        """Retire the notes the room has already settled.

        Each note is handed its own evidence — the messages said since it
        was raised — because a `closed` verdict is only honoured when it
        cites a row from that note's own evidence. Curating without the
        evidence would mean every close is refused, which looks exactly
        like a model that never closes anything.
        """
        rows = await _open_notes(self._store, self._config.workspace)
        if not rows:
            return
        for_curation: list[orient.NoteForCuration] = []
        for r in rows:
            evidence_rows = await self._store.fetch(
                """SELECT id, sender_id, is_ora, ts, body
                     FROM messages
                    WHERE platform = $1 AND conversation_id = $2 AND ts >= $3
                    ORDER BY id ASC LIMIT 30""",
                room.platform,
                room.conversation_id,
                r["created_at"],
            )
            for_curation.append(
                orient.NoteForCuration(
                    id=r["id"],
                    title=r["title"],
                    closing_condition=r["closing_condition"],
                    anchor_at=None,
                    evidence=[
                        orient.MessageRow(
                            id=e["id"],
                            sender_label=(
                                "Ora"
                                if e["is_ora"]
                                else (
                                    p.name
                                    if (p := self._people.resolve(room.platform, e["sender_id"]))
                                    else (e["sender_id"] or "someone")
                                )
                            ),
                            body=e["body"],
                            ts=e["ts"],
                            is_ora=e["is_ora"],
                        )
                        for e in evidence_rows
                    ],
                )
            )

        result = await orient.curate(self._model, self._store, notes=for_curation, now=now)
        for retired in result.retired:
            self._sink.event(
                "CURATION", room.label, f"note {retired.id} {retired.reason}",
                platform=room.platform,
            )

    # ---- the whole workspace -------------------------------------------

    async def run_proactive(self) -> None:
        """The one path that speaks with nobody having said anything."""
        clocks = self._config.clocks
        while True:
            try:
                await asyncio.sleep(clocks.proactive_tick_seconds)
                now = datetime.now(UTC)
                workspace = self._config.workspace
                home = decide_proactive.home_room(self._config)
                if home is None:
                    # Say so, loudly and once. This exact failure already
                    # happened: home_room() returned None, the proactive path
                    # declined forever, and nothing anywhere said why — the
                    # demo's one unprompted moment would simply never arrive.
                    # A silent `continue` is what made it invisible.
                    if not self._warned_no_home:
                        self._warned_no_home = True
                        self._sink.event(
                            "PROACTIVE", "-",
                            "no home room resolved from rooms.toml — proactive disabled",
                            error=True,
                        )
                    continue

                due = await self._store.fetch(
                    """SELECT id, workspace, note_id, due_at, intent
                         FROM reminders
                        WHERE workspace = $1 AND state = 'pending' AND due_at <= $2
                        ORDER BY due_at ASC""",
                    workspace,
                    now,
                )
                note_rows = await _open_notes(self._store, workspace)
                transcript = await _transcript(self._store, home, self._people)
                standing = await _standing(self._store, home)
                open_notes = [
                    decide_proactive.OpenNote(
                        id=r["id"],
                        title=r["title"],
                        closing_condition=r["closing_condition"],
                        created_at=r["created_at"],
                        room_label=r["room_label"],
                        strikes=r["strikes"],
                        revisit_at=r["revisit_at"],
                    )
                    for r in note_rows
                ]

                for d in due:
                    outcome = await decide_proactive.act(
                        self._model,
                        self._store,
                        self._config,
                        self._people,
                        reminder=decide_proactive.PendingReminder(
                            id=d["id"],
                            workspace=d["workspace"],
                            note_id=d["note_id"],
                            due_at=d["due_at"],
                            intent=d["intent"],
                        ),
                        notes=open_notes,
                        standing=standing,
                        loop_decisions=await _loop_decisions(
                            self._store, workspace, ("proactive_decide", "proactive_act")
                        ),
                        transcript=transcript,
                        now=now,
                    )
                    self._sink.event("ACT", home.label, f"reminder {d['id']}: {outcome.verdict}",
                                     platform=home.platform,
                                     error=outcome.verdict == "failed")
                    if outcome.verdict == "spoke":
                        await self._show(getattr(outcome, "text", "") or d["intent"], home)

                outcome = await decide_proactive.decide(
                    self._model,
                    self._store,
                    workspace=workspace,
                    platform=home.platform,
                    conversation_id=home.conversation_id,
                    notes=open_notes,
                    standing=standing,
                    loop_decisions=await _loop_decisions(
                        self._store, workspace, ("proactive_decide", "proactive_act")
                    ),
                    transcript=transcript,
                    now=now,
                )
                raised = len(outcome.reminders_created)
                what = f"{raised} reminder(s) raised" if raised else "decline"
                if outcome.struck_notes:
                    what += f", {len(outcome.struck_notes)} struck"
                self._sink.event("PROACTIVE", home.label, what,
                                 platform=home.platform, error=not outcome.ok)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the tick must not kill the loop
                self._sink.event("PROACTIVE", "-", f"tick failed: {type(exc).__name__}", error=True)


async def _forever(sink: LogSink, what: str, run: Any, *, backoff: float = 3.0) -> None:
    """Run a listener forever, restarting it when it dies.

    Neither observer has an internal guard: `run()` opens an SSE stream and
    lets anything the transport raises escape. A signal-cli reconnect or one
    malformed payload would otherwise end that task for the rest of the
    session, and the room it feeds goes quiet with nothing in the log
    explaining why — the worst failure shape there is.
    """
    while True:
        try:
            await run()
            sink.event("OBSERVE", "-", f"{what} stream ended; reconnecting")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a listener must always come back
            sink.event("OBSERVE", "-", f"{what} died ({type(exc).__name__}); reconnecting",
                       error=True)
        await asyncio.sleep(backoff)


async def main() -> None:
    config = load_config()
    store = await Store.connect(config.env.database_url)
    await store.apply_schema()

    sink = LogSink(json_path=Path(config.env.ora_log))
    people = PeopleDirectory(load_people())

    import os

    # Before the OpenAI client is built, not after: the Langfuse drop-in
    # patches at instantiation, so a client created first is never traced.
    langfuse = tracing.init()
    client = tracing.traced_openai(
        base_url=config.env.model_base_url,
        api_key=os.environ.get("OLLAMA_CLOUD_API_KEY", ""),
    )
    model = ModelClient(
        client=client,
        model=config.env.model,
        timeout_seconds=config.clocks.model_timeout_seconds,
        store=store,
    )

    honcho = None
    if config.env.honcho_base_url and config.env.honcho_workspace:
        honcho = HonchoClient(
            base_url=config.env.honcho_base_url,
            workspace_id=config.env.honcho_workspace,
            ai_peer=config.env.honcho_ai_peer or "ora",
            # 2s (the default) is under the cold-start cost of the first
            # write, which reported a failure for a row that had in fact
            # landed. A false failure on a memory write is worse than a slow
            # one: it is the kind of thing that gets "fixed" by retrying and
            # quietly doubles the row.
            feed_timeout=6.0,
        )
        await honcho.ensure_workspace()

    search_provider = (
        ExaSearchProvider(api_key=config.env.exa_api_key) if config.env.exa_api_key else None
    )

    # The face. Optional by construction: if the port is taken or nobody
    # opens the page, `start()` returns False and every later `speak()` is a
    # no-op — a stage prop must never be able to affect a real send.
    presenter = present.Presenter()
    if await presenter.start():
        sink.event("OBSERVE", "-", "face up at http://127.0.0.1:8765/")
    else:
        sink.event("OBSERVE", "-", "face unavailable; continuing without it")

    loop = Loop(
        config, store, sink, people, model,
        vision_client=client, honcho=honcho, search_provider=search_provider,
        presenter=presenter,
    )
    rooms = list(config.rooms.values())
    own = await loop.learn_own_ids()
    sink.event(
        "OBSERVE", "-",
        f"up: {len(rooms)} room(s), workspace {config.workspace}, {len(own)} own id(s)",
    )

    tasks = [
        asyncio.create_task(
            _forever(sink, "signal-cli", SignalObserver(config, store, sink, people, client).run)
        ),
        asyncio.create_task(
            _forever(sink, "whatsapp-bridge", WhatsAppObserver(config, store, sink, people).run)
        ),
        asyncio.create_task(loop.run_proactive()),
        *[asyncio.create_task(loop.run_room(r)) for r in rooms],
    ]
    try:
        # return_exceptions=True, deliberately: the default lets ONE task's
        # exception propagate out of gather, and the `finally` below then
        # cancels every other task. The room loops' own "must not kill the
        # loop" guards are worthless under that — a single transient
        # reconnect in one observer would take the whole agent dark.
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        tracing.flush(langfuse)
        await presenter.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await store.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
