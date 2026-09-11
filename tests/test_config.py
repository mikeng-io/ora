from pathlib import Path

from loop.config import Clocks, load_rooms_toml

ROOMS_TOML = """
[workspace.demo]
home_room = "signal:g1"
description = "the demo circle"

[[room]]
platform = "signal"
conversation_id = "g1"
label = "Signal"
description = "the main room"

[[room]]
platform = "whatsapp"
conversation_id = "j1@g.us"
label = "WhatsApp"
"""


def _write_rooms(tmp_path: Path) -> Path:
    p = tmp_path / "rooms.toml"
    p.write_text(ROOMS_TOML)
    return p


def test_unlisted_room_is_none(tmp_path: Path) -> None:
    from loop.config import Config, Env

    _, rooms = load_rooms_toml(_write_rooms(tmp_path))
    cfg = Config(env=Env(), clocks=Clocks(), rooms=rooms)
    assert cfg.room_for("signal", "g1") is not None
    assert cfg.room_for("signal", "not-listed") is None
    assert cfg.room_for("discord", "g1") is None


def test_listed_room_resolves_label(tmp_path: Path) -> None:
    _, rooms = load_rooms_toml(_write_rooms(tmp_path))
    room = rooms[("whatsapp", "j1@g.us")]
    assert room.label == "WhatsApp"


def test_clocks_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SETTLE_SECONDS", "99")
    monkeypatch.setenv("COOLDOWN_SECONDS", "5")
    clocks = Clocks.from_env()
    assert clocks.settle_seconds == 99
    assert clocks.cooldown_seconds == 5
    assert clocks.fold_batch == 6  # default, untouched


def test_clocks_default_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SETTLE_SECONDS", raising=False)
    clocks = Clocks.from_env()
    assert clocks.settle_seconds == 15
