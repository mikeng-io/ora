"""Pure-function coverage for loop/memory.py (design/07 item 12). The
network paths (feed/peer_card/representation) are verified live against
norty's Honcho, workspace `ora-verify` only — see the item's commit."""

from loop.memory import Recollection, _conclusions, _safe_conversation_key


def test_safe_key_passes_through_a_legal_id() -> None:
    assert _safe_conversation_key("verify-room-1") == "verify-room-1"


def test_safe_key_translates_base64_to_urlsafe() -> None:
    # A Signal group id's base64 alphabet (+, /, =) 422s the session-create
    # endpoint; translated to the url-safe alphabet instead of hashed.
    key = _safe_conversation_key("Lhof+abc/def=")
    assert key == "Lhof-abc_def"
    assert "+" not in key and "/" not in key and "=" not in key


def test_safe_key_hashes_when_still_illegal() -> None:
    key = _safe_conversation_key("has spaces and 中文")
    assert len(key) == 64  # sha256 hex


def test_conclusions_extracts_explicit_observations_only() -> None:
    body = (
        "## Explicit Observations\n"
        "[2026-08-19 10:17:08] agreed to speak all Chinese\n"
        "\n"
        "## Deductive Observations\n"
        "[2026-08-19] likes hiking on weekends\n"
        "   Premises:\n"
        "   - went hiking last Saturday\n"
    )
    out = _conclusions(body)
    assert out == ("agreed to speak all Chinese", "likes hiking on weekends")


def test_conclusions_drops_pattern_and_contradiction_labels() -> None:
    body = (
        "## Inductive Observations\n"
        " **Pattern** [high]: prefers Cantonese in the group\n"
        "   **Type**: linguistic\n"
        "   **Sources**:\n"
        "   - a message in Cantonese\n"
        "\n"
        "## Contradictions\n"
        " **CONTRADICTION**: said two different restaurants\n"
        "   **Conflicting statements**:\n"
        "   - one message\n"
    )
    out = _conclusions(body)
    assert out == ("prefers Cantonese in the group", "said two different restaurants")


def test_conclusions_non_string_input_is_empty() -> None:
    assert _conclusions(None) == ()
    assert _conclusions(42) == ()


def test_conclusions_empty_string_is_empty() -> None:
    assert _conclusions("") == ()


def test_recollection_unreachable_carries_no_conclusions() -> None:
    r = Recollection(reachable=False)
    assert r.conclusions == ()
