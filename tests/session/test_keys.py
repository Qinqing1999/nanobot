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


# -- Non-word chat_id tests (e.g. WeChat group IDs) --------------------


def test_parse_wechat_group_id() -> None:
    """WeChat group chat IDs contain '@' which is not a word char."""
    channel, chat_id, index = parse_session_key("weixin:12345@chatroom")
    assert channel == "weixin"
    assert chat_id == "12345@chatroom"
    assert index == 0


def test_parse_wechat_group_id_with_index() -> None:
    """WeChat group ID with session index suffix."""
    channel, chat_id, index = parse_session_key("weixin:12345@chatroom:1")
    assert channel == "weixin"
    assert chat_id == "12345@chatroom"
    assert index == 1


def test_session_base_key_wechat_group() -> None:
    """Base key extraction for WeChat group IDs."""
    assert session_base_key("weixin:12345@chatroom") == "weixin:12345@chatroom"
    assert session_base_key("weixin:12345@chatroom:1") == "weixin:12345@chatroom"
    assert session_base_key("weixin:12345@chatroom:10") == "weixin:12345@chatroom"


def test_parse_chat_id_with_hyphen() -> None:
    """Chat IDs with hyphens should parse correctly."""
    channel, chat_id, index = parse_session_key("discord:user-abc-123")
    assert channel == "discord"
    assert chat_id == "user-abc-123"
    assert index == 0


def test_parse_chat_id_with_hyphen_and_index() -> None:
    """Chat IDs with hyphens and index suffix."""
    channel, chat_id, index = parse_session_key("discord:user-abc-123:2")
    assert channel == "discord"
    assert chat_id == "user-abc-123"
    assert index == 2


def test_session_base_key_hyphenated() -> None:
    """Base key extraction for hyphenated chat IDs."""
    assert session_base_key("discord:user-abc-123:2") == "discord:user-abc-123"


def test_parse_numeric_chat_id_no_false_index() -> None:
    """'weixin:123' should NOT be parsed as having index 123."""
    channel, chat_id, index = parse_session_key("weixin:123")
    assert channel == "weixin"
    assert chat_id == "123"
    assert index == 0


def test_session_base_key_numeric_chat_id() -> None:
    """'weixin:123' base key is itself (no index to strip)."""
    assert session_base_key("weixin:123") == "weixin:123"


def test_round_trip_weixin_group() -> None:
    """session_key_for_channel → parse_session_key round-trip for WeChat groups."""
    key = session_key_for_channel("weixin", "12345@chatroom", session_index=3)
    assert key == "weixin:12345@chatroom:3"
    channel, chat_id, index = parse_session_key(key)
    assert channel == "weixin"
    assert chat_id == "12345@chatroom"
    assert index == 3
    assert session_base_key(key) == "weixin:12345@chatroom"
