"""Env + rooms.toml + clocks — one place; no constants in modules.

Deny-by-default room lookup: `Config.room_for` returns `None`
for anything not listed in `rooms.toml`; every stage that observes or sends
must go through it before touching a store or a wire.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from Ora's own .env.

    Ora's `.env` WINS over an inherited shell variable. That is the opposite
    of the usual convention and it is deliberate: this machine exports
    `HONCHO_WORKSPACE` pointing at a different, production workspace, and
    the usual "don't override the environment" rule silently aimed Ora's
    memory writes at it. Measured, not theorised — the first live write went
    to the wrong workspace and came back 500.

    A shared name that means two different things on one machine is a
    hazard, so the file next to the code decides what this process does.
    Anything genuinely per-machine (a key, a host) simply is not listed in
    `.env` and still falls through to the environment.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #", 1)[0].strip()
        if key:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Room:
    platform: str
    conversation_id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class Workspace:
    name: str
    home_room: str
    description: str = ""


@dataclass(frozen=True)
class Clocks:
    settle_seconds: int = 15
    max_window_messages: int = 6
    cooldown_seconds: int = 60
    fold_batch: int = 6
    notes_tick_seconds: int = 30
    notes_quiet_seconds: int = 0
    proactive_tick_seconds: int = 30
    remind_lead_seconds: int = 60
    model_timeout_seconds: int = 20
    gate_timeout_seconds: int = 8

    @classmethod
    def from_env(cls) -> Clocks:
        return cls(
            settle_seconds=_env_int("SETTLE_SECONDS", cls.settle_seconds),
            max_window_messages=_env_int("MAX_WINDOW_MESSAGES", cls.max_window_messages),
            cooldown_seconds=_env_int("COOLDOWN_SECONDS", cls.cooldown_seconds),
            fold_batch=_env_int("FOLD_BATCH", cls.fold_batch),
            notes_tick_seconds=_env_int("NOTES_TICK_SECONDS", cls.notes_tick_seconds),
            notes_quiet_seconds=_env_int("NOTES_QUIET_SECONDS", cls.notes_quiet_seconds),
            proactive_tick_seconds=_env_int("PROACTIVE_TICK_SECONDS", cls.proactive_tick_seconds),
            remind_lead_seconds=_env_int("REMIND_LEAD_SECONDS", cls.remind_lead_seconds),
            model_timeout_seconds=_env_int("MODEL_TIMEOUT_SECONDS", cls.model_timeout_seconds),
            gate_timeout_seconds=_env_int("GATE_TIMEOUT_SECONDS", cls.gate_timeout_seconds),
        )


@dataclass(frozen=True)
class Env:
    signal_base_url: str = ""
    signal_account: str = ""
    whatsapp_base_url: str = ""
    honcho_base_url: str = ""
    honcho_workspace: str = ""
    honcho_ai_peer: str = ""
    model_base_url: str = ""
    model: str = ""
    reasoning_gate: str = "none"
    reasoning_turn: str = "low"
    google_map_api_key: str = ""
    exa_api_key: str = ""
    # Where "how do we get there" starts from when nobody says an origin.
    ora_default_origin: str = "Tin Shui Wai, Hong Kong"
    database_url: str = ""
    ora_log: str = "./ora.log"
    ora_tz: str = "Asia/Hong_Kong"

    @classmethod
    def from_env(cls) -> Env:
        return cls(
            signal_base_url=os.environ.get("SIGNAL_BASE_URL", ""),
            signal_account=os.environ.get("SIGNAL_ACCOUNT", ""),
            whatsapp_base_url=os.environ.get("WHATSAPP_BASE_URL", ""),
            honcho_base_url=os.environ.get("HONCHO_BASE_URL", ""),
            honcho_workspace=os.environ.get("HONCHO_WORKSPACE", ""),
            honcho_ai_peer=os.environ.get("HONCHO_AI_PEER", ""),
            model_base_url=os.environ.get("MODEL_BASE_URL", ""),
            model=os.environ.get("MODEL", ""),
            reasoning_gate=os.environ.get("REASONING_GATE", "none"),
            reasoning_turn=os.environ.get("REASONING_TURN", "low"),
            google_map_api_key=os.environ.get("GOOGLE_MAP_API_KEY", ""),
            exa_api_key=os.environ.get("EXA_API_KEY", ""),
            ora_default_origin=os.environ.get("ORA_DEFAULT_ORIGIN", "Tin Shui Wai, Hong Kong"),
            database_url=os.environ.get("DATABASE_URL", ""),
            ora_log=os.environ.get("ORA_LOG", "./ora.log"),
            ora_tz=os.environ.get("ORA_TZ", "Asia/Hong_Kong"),
        )

    @staticmethod
    def api_key(name: str) -> str:
        """Read a key straight from the environment. Never log or print the result."""
        return os.environ.get(name, "")


@dataclass(frozen=True)
class Config:
    env: Env
    clocks: Clocks
    workspaces: dict[str, Workspace] = field(default_factory=dict)
    rooms: dict[tuple[str, str], Room] = field(default_factory=dict)

    def room_for(self, platform: str, conversation_id: str) -> Room | None:
        """Deny-by-default: `None` for anything not in rooms.toml."""
        return self.rooms.get((platform, conversation_id))

    @property
    def workspace(self) -> str:
        """Ora is one workspace of two rooms and one loop —
        `rooms.toml` names exactly one `[workspace.*]` table in v1, and every
        row belongs to it."""
        if not self.workspaces:
            return "demo"
        return next(iter(self.workspaces))


def load_rooms_toml(
    path: str | Path = "rooms.toml",
) -> tuple[dict[str, Workspace], dict[tuple[str, str], Room]]:
    p = Path(path)
    if not p.is_file():
        return {}, {}
    data = tomllib.loads(p.read_text())
    workspaces: dict[str, Workspace] = {}
    for name, w in data.get("workspace", {}).items():
        workspaces[name] = Workspace(
            name=name,
            home_room=w.get("home_room", ""),
            description=w.get("description", ""),
        )
    rooms: dict[tuple[str, str], Room] = {}
    for r in data.get("room", []):
        room = Room(
            platform=r["platform"],
            conversation_id=r["conversation_id"],
            label=r["label"],
            description=r.get("description", ""),
        )
        rooms[(room.platform, room.conversation_id)] = room
    return workspaces, rooms


def load_config(
    rooms_path: str | Path = "rooms.toml",
    dotenv_path: str | Path = ".env",
) -> Config:
    _load_dotenv(Path(dotenv_path))
    workspaces, rooms = load_rooms_toml(rooms_path)
    return Config(
        env=Env.from_env(),
        clocks=Clocks.from_env(),
        workspaces=workspaces,
        rooms=rooms,
    )
