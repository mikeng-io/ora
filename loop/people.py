"""`people.toml` -> (platform, id) -> person, name; reverse map for sends.
The only place a human's two ids meet.

An unresolvable name goes out as plain text, never a guess (the reference project's #211's
rule) — `reverse` returning `None` is that contract's other half.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    signal: str = ""
    whatsapp: str = ""
    # WhatsApp delivers `sender_jid` as a LID (`2702…@lid`), not the phone
    # jid, so inbound resolution misses on `whatsapp` alone — measured live
    # on the first real message. Both are indexed for resolve(); reverse()
    # still returns `whatsapp`, because an outbound mention has to carry the
    # phone form to render as a name in the room.
    whatsapp_lid: str = ""


def load_people(path: str | Path = "people.toml") -> list[Person]:
    p = Path(path)
    if not p.is_file():
        return []
    data = tomllib.loads(p.read_text())
    return [
        Person(
            id=row["id"],
            name=row["name"],
            signal=row.get("signal", ""),
            whatsapp=row.get("whatsapp", ""),
            whatsapp_lid=row.get("whatsapp_lid", ""),
        )
        for row in data.get("person", [])
    ]


class PeopleDirectory:
    def __init__(self, people: list[Person]) -> None:
        self._by_platform_id: dict[tuple[str, str], Person] = {}
        self._by_name: dict[str, Person] = {}
        for person in people:
            if person.signal:
                self._by_platform_id[("signal", person.signal)] = person
            if person.whatsapp:
                self._by_platform_id[("whatsapp", person.whatsapp)] = person
            if person.whatsapp_lid:
                self._by_platform_id[("whatsapp", person.whatsapp_lid)] = person
            self._by_name[person.name] = person

    def resolve(self, platform: str, sender_id: str) -> Person | None:
        """`sender_id -> person_id` (Observe's own join). `None` for an
        unmapped account — never a guess."""
        return self._by_platform_id.get((platform, sender_id))

    def reverse(self, platform: str, name: str) -> str | None:
        """`@Name -> platform id`, for a real mention (act.py). `None` for
        an unresolvable name — the caller's fallback is plain text, never a
        guess (the reference project's #211)."""
        person = self._by_name.get(name)
        if person is None:
            return None
        platform_id = person.signal if platform == "signal" else person.whatsapp
        return platform_id or None

    def names_longest_first(self) -> list[str]:
        """Every known name, longest first — `act.py`'s `@handle` scan needs
        longest-match so a shorter name that prefixes a longer one never
        steals the match (the reference project `outbound._handle_index`'s rule)."""
        return sorted(self._by_name, key=len, reverse=True)
