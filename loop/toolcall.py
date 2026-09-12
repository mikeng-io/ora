"""Real tool calling: the model asks for a lookup, we run it, it answers.

An earlier cut of this did it backwards — keyword-matched the message, ran
the tools first, and pasted the results into the prompt. Two things were
wrong with that. The regexes could not tell «點樣去中環» from «中環好唔好玩»,
and there was a language model sitting right there whose whole competence is
reading what someone meant. Ollama Cloud's `deepseek-v4-flash` supports
OpenAI tool calling, verified live: asked «聽日香港落唔落雨？» it returned
`finish_reason=tool_calls` with `{"city":"Hong Kong","when":"tomorrow"}`.

So the model decides. This module gives it a toolbox, runs whatever it asks
for, feeds the results back, and repeats until it answers — the ordinary
agentic loop, with three properties that are not ordinary:

- **Bounded.** `max_rounds` caps the conversation. A model that loops asking
  for tools forever would hang a turn in a live group chat, and a hung turn
  looks exactly like a broken agent.
- **Every call is traced.** Each round writes its own `model_calls` row
  (ORA-17), so a turn that took four rounds leaves four rows, not one.
- **What actually ran is recorded.** `executed` holds the tools that
  returned something. Grounding is decided from THAT, never from the
  model's account of itself — the model does not get to claim it checked.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("ora.toolcall")

MAX_ROUNDS = 4


@dataclass
class ToolLoopResult:
    """The end of the conversation, plus what happened on the way."""

    content: str = ""
    ok: bool = False
    finish_reason: str = ""
    call_ids: list[int] = field(default_factory=list)
    executed: dict[str, dict[str, Any]] = field(default_factory=dict)
    rounds: int = 0
    error_kind: str | None = None

    @property
    def call_id(self) -> int | None:
        """The LAST model call — the one that produced the answer. The whole
        chain is in `call_ids`; a row that cites one number should cite the
        call that actually wrote the text."""
        return self.call_ids[-1] if self.call_ids else None

    @property
    def grounded(self) -> frozenset[str]:
        """`tool:<name>` for every tool that ran AND came back with
        something. A tool that answered `unavailable` or `nothing_found` is
        not grounding: crediting it would put a citation on an answer that
        read nothing."""
        out = set()
        for name, result in self.executed.items():
            if isinstance(result, dict) and result.get("status") == "ok":
                out.add(f"tool:{name}")
        return frozenset(out)


def _schema() -> list[dict[str, Any]]:
    """What Ora can look up. Descriptions are written for the model, and
    they say when NOT to call — an agent that searches the web for something
    the room already said is worse than one that stays quiet."""
    return [
        {
            "type": "function",
            "function": {
                "name": "route",
                "description": (
                    "Public transport directions between two places in Hong Kong. "
                    "Call this only when someone is asking how to GET somewhere."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string", "description": "Start; empty means from here"},
                        "destination": {"type": "string"},
                    },
                    "required": ["destination"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "weather",
                "description": (
                    "Hong Kong weather now or tomorrow. Only when the weather itself "
                    "is being asked about."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"when": {"type": "string", "enum": ["now", "tomorrow"]}},
                    "required": ["when"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": (
                    "Search the web for a fact this group cannot answer from its own "
                    "conversation. Not for opinions, plans, or anything already said "
                    "in the transcript."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch",
                "description": "Read a web page someone linked, when they are asking about it.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
    ]


class Toolbox:
    """Runs what the model asks for. Every tool is already fail-closed on
    its own; this adds the outer net so a tool that breaks in a new way
    costs its answer a citation rather than ending the turn."""

    def __init__(
        self,
        *,
        route_fn: Any = None,
        route_api_key: str = "",
        default_origin: str = "",
        search_provider: Any = None,
        weather_fn: Any = None,
    ) -> None:
        self._route_fn = route_fn
        self._route_api_key = route_api_key
        self._default_origin = default_origin
        self._search = search_provider
        self._weather = weather_fn

    def schema(self) -> list[dict[str, Any]]:
        return _schema()

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "route":
                if self._route_fn is None or not self._route_api_key:
                    return {"status": "unavailable", "note": "route not configured"}
                destination = str(args.get("destination") or "").strip()
                if not destination:
                    return {"status": "nothing_found", "note": "no destination given"}
                origin = str(args.get("origin") or "").strip() or self._default_origin
                return await self._route_fn(
                    api_key=self._route_api_key,
                    origin=origin,
                    destination=destination,
                    mode="TRANSIT",
                )
            if name == "weather":
                if self._weather is None:
                    return {"status": "unavailable", "note": "weather not configured"}
                return await self._weather(when=str(args.get("when") or "now"))
            if name == "search":
                if self._search is None:
                    return {"status": "unavailable", "note": "search not configured"}
                return await self._search.search(query=str(args.get("query") or ""))
            if name == "fetch":
                if self._search is None:
                    return {"status": "unavailable", "note": "fetch not configured"}
                return await self._search.fetch(url=str(args.get("url") or ""))
        except Exception as exc:  # noqa: BLE001 — a broken tool costs a citation, not the turn
            log.warning("tool %s failed: %s", name, type(exc).__name__)
            return {"status": "unavailable", "note": type(exc).__name__}
        return {"status": "unavailable", "note": f"unknown tool {name}"}


async def run(
    model: Any,
    toolbox: Toolbox,
    *,
    stage: str,
    system: str,
    prompt: str,
    reasoning_effort: str = "low",
    platform: str | None = None,
    conversation_id: str | None = None,
    max_rounds: int = MAX_ROUNDS,
) -> ToolLoopResult:
    """Let the model work, giving it tools, until it answers.

    Never raises into the loop: every failure — a timeout, a transport
    error, a malformed tool argument, exhausting the round budget — ends as
    a result with `ok=False` that the caller reduces to silence.
    """
    result = ToolLoopResult()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    for round_index in range(max_rounds):
        result.rounds = round_index + 1
        try:
            completion = await model.complete_with_tools(
                stage=stage,
                messages=messages,
                tools=toolbox.schema(),
                reasoning_effort=reasoning_effort,
                platform=platform,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("tool loop model call failed: %s", type(exc).__name__)
            result.error_kind = type(exc).__name__
            return result

        if completion.call_id is not None:
            result.call_ids.append(completion.call_id)
        result.finish_reason = completion.finish_reason

        if not completion.tool_calls:
            result.content = completion.content
            result.ok = completion.ok
            result.error_kind = completion.error_kind
            return result

        # Echo the assistant's own tool request back before the results, or
        # the next round has no record of what it asked for.
        messages.append(completion.assistant_message)
        for call in completion.tool_calls:
            name = call.get("name") or ""
            try:
                args = json.loads(call.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_result = await toolbox.call(name, args)
            result.executed[name] = tool_result
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": json.dumps(tool_result, ensure_ascii=False)[:4000],
                }
            )

    # Out of rounds. Deliberately not "answer anyway": a model still asking
    # for tools has not reached an answer, and inventing one here is the
    # failure mode the whole grounding discipline exists to prevent.
    result.error_kind = "max_rounds"
    return result
