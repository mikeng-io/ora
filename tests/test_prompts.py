"""Every copied prompt's sha must match its manifest line — the prefab
prompts are never edited (design/00 §4, ORA-1). `turn.md` is the one prompt
assembled tonight; it must name every block `render.py` emits."""

import hashlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# path -> sha12, as recorded in MANIFEST.md, recomputed from the nora tree
# tonight (design/07 ground rules: recompute, do not trust 03 §1 blindly).
EXPECTED_SHA12 = {
    "gate.md": "2d3012ecbdaa",
    "fold.md": "e6539a3abeb0",
    "verdict.md": "1d96241861cb",
    "notes.md": "5de105577f58",
    "curation.md": "39e362cd5799",
    "proactive.md": "8cfc424dbc58",
}

RENDER_BLOCKS = ["transcript", "standing", "notes", "loop_decisions", "self_card", "peer_card"]


def _sha12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def test_copied_prompts_match_their_manifest_sha() -> None:
    for name, expected in EXPECTED_SHA12.items():
        path = PROMPTS_DIR / name
        assert path.is_file(), f"{name} missing from prompts/"
        assert _sha12(path) == expected, f"{name} sha drifted from the manifest — was it edited?"


def test_turn_md_names_every_block_render_emits() -> None:
    text = (PROMPTS_DIR / "turn.md").read_text().lower()
    for block in RENDER_BLOCKS:
        assert block in text, f"turn.md never names the {block!r} block"


def test_turn_md_carries_the_grounded_on_and_cancel_clauses() -> None:
    text = (PROMPTS_DIR / "turn.md").read_text().lower()
    assert "grounded_on" in text  # ORA-10
    assert "cancel" in text  # ORA-15


def test_all_seven_prompts_present() -> None:
    expected = set(EXPECTED_SHA12) | {"turn.md"}
    present = {p.name for p in PROMPTS_DIR.glob("*.md")}
    assert expected <= present
