"""Plain local storage for image bytes (ORA-18) — no content-addressed
custody, no encryption, no rustfs. `Note.closing_condition`-grade honesty
about the size of this: the spec is the right shape
for a production deployment with retention and a taxonomy; this is one
demo night's worth of images, kept as files on disk, keyed by sha256 so a
second copy of the same bytes never lands twice.

Files live under `./state/media/<sha256>.<ext>` — a sibling of
`./state/signal-cli` and `./state/whatsapp`, never inside either (Ora has
no business reading transport state, and vice versa).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# The placeholder's id length (`[image abc123]`) — shared so a caller writing
# the placeholder (observe_signal.py) and a caller joining media into it at
# render time (render.py) can never drift apart on how many hex chars a
# shortid is.
MEDIA_SHORTID_LEN = 6

_EXT_BY_MEDIA_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
}


def extension_for(media_type: str) -> str:
    return _EXT_BY_MEDIA_TYPE.get(media_type.lower(), "bin")


@dataclass(frozen=True)
class StoredMedia:
    sha256: str
    path: str
    size: int
    already_stored: bool  # True if this sha256 was already on disk


def store(data: bytes, *, media_type: str, root: str | Path = "./state/media") -> StoredMedia:
    """Write `data` under its sha256, once. A second call with the SAME
    bytes is a no-op (the file already exists) rather than a second write —
    the whole point of content-addressing at this scale."""
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    path = root_path / f"{digest}.{extension_for(media_type)}"
    already_stored = path.exists()
    if not already_stored:
        path.write_bytes(data)
    return StoredMedia(sha256=digest, path=str(path), size=len(data), already_stored=already_stored)


def read(path: str | Path) -> bytes:
    return Path(path).read_bytes()
