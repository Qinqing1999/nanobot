"""Tests for session key encoding with index suffixes."""

from __future__ import annotations

from nanobot.session.keys import (
    UNIFIED_SESSION_KEY,
    parse_session_key,
    session_base_key,
    session_key_for_channel,
)


def test_basic_session_key_no_suffix() -> None:
    """channel:chat_id → index=0, no suffix."""
    assert session_key_for_channel("telegram", "12345") == "telegram:12345"
    assert session_key_for_channel("telegram", "12345", session_index=0) == "telegram:12345"


def test_session_key_with_index_suffix() -> None:
    """session_index > 0 → append :N suffix."""
    assert session_key_for_channel("telegram", "12345", session_index=1) == "telegram:12345:1"
    assert session_key_for_channel("telegram", "12345", session_index=3) == "telegram:12345:3"


def test_parse_basic_key() -> None:
    """Parse channel:chat_id → (channel, chat_id, 0)."""
    channel, chat_id, index = parse_session_key("telegram:12345")
    assert channel == "telegram"
    assert chat_id == "12345"
    assert index == 0


def test_parse_suffixed_key() -> None:
    """Parse channel:chat_id:2 → (channel, chat_id, 2)."""
    channel, chat_id, index = parse_session_key("telegram:12345:2")
    assert channel == "telegram"
    assert chat_id == "12345"
    assert index == 2


def test_parse_key_with_colons_in_chat_id() -> None:
    """Keys with multiple colons parse correctly — last :N is the index."""
    # chat_id that contains colons is not expected, but the parser
    # should still work: it matches \w+:\w+ at the start
    channel, chat_id, index = parse_session_key("cli:test_session:3")
    assert channel == "cli"
    assert chat_id == "test_session"
    assert index == 3


def test_session_base_key_strips_suffix() -> None:
    """Extract base key without index suffix."""
    assert session_base_key("telegram:12345") == "telegram:12345"
    assert session_base_key("telegram:12345:2") == "telegram:12345"
    assert session_base_key("telegram:12345:10") == "telegram:12345"


def test_session_base_key_no_colon() -> None:
    """Edge case: key without colon returns as-is."""
    assert session_base_key("unknown") == "unknown"


def test_unified_session_key() -> None:
    """Unified session key should pass through unchanged."""
    assert session_base_key(UNIFIED_SESSION_KEY) == UNIFIED_SESSION_KEY
    ch, cid, idx = parse_session_key(UNIFIED_SESSION_KEY)
    assert idx == 0
