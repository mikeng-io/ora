import hashlib

from loop.media_store import extension_for, read, store


def test_store_writes_the_file_keyed_by_sha256(tmp_path) -> None:
    data = b"fake jpeg bytes"
    result = store(data, media_type="image/jpeg", root=tmp_path)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.path.endswith(f"{result.sha256}.jpg")
    assert result.size == len(data)
    assert not result.already_stored
    assert read(result.path) == data


def test_store_is_idempotent_for_the_same_bytes(tmp_path) -> None:
    data = b"same bytes twice"
    first = store(data, media_type="image/png", root=tmp_path)
    second = store(data, media_type="image/png", root=tmp_path)
    assert first.sha256 == second.sha256
    assert not first.already_stored
    assert second.already_stored


def test_extension_for_known_and_unknown_types() -> None:
    assert extension_for("image/jpeg") == "jpg"
    assert extension_for("image/png") == "png"
    assert extension_for("application/octet-stream") == "bin"
    assert extension_for("") == "bin"


def test_store_creates_the_root_directory(tmp_path) -> None:
    root = tmp_path / "nested" / "media"
    result = store(b"x", media_type="image/webp", root=root)
    assert root.is_dir()
    assert read(result.path) == b"x"
