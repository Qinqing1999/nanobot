"""Shared session key constants and helpers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

UNIFIED_SESSION_KEY = "unified:default"
LAST_CHANNEL_METADATA_KEY = "last_channel"

# Session keys are formatted as ``channel:chat_id`` with an optional ``:N``
# suffix for multi-session support (e.g. ``weixin:12345@chatroom:2``).
# The previous regex ``^(\w+:\w+)(?::(\d+))?$`` only matched word
# characters in chat_id, breaking for IDs containing ``@``, ``-``, etc.
# We now use a split-based approach in parse_session_key / session_base_key
# so that any character is allowed in chat_id.


def session_key_for_channel(
    channel: str,
    chat_id: str,
    *,
    unified_session: bool = False,
    session_index: int | None = None,
) -> str:
    """Return the session key for a channel/chat pair.

    When ``session_index`` > 0, appends ``:N`` suffix for multi-session support.
    """
    if unified_session:
        return UNIFIED_SESSION_KEY
    base = f"{channel}:{chat_id}"
    if session_index is not None and session_index > 0:
        return f"{base}:{session_index}"
    return base


def remember_last_channel(
    metadata: MutableMapping[str, Any],
    channel: str,
    chat_id: str,
) -> None:
    """Persist the latest concrete delivery route in session metadata."""
    if not channel or not chat_id:
        return
    metadata[LAST_CHANNEL_METADATA_KEY] = f"{channel}:{chat_id}"


def last_channel_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Return a concrete delivery route from persisted session metadata."""
    if not isinstance(metadata, Mapping):
        return None
    route = metadata.get(LAST_CHANNEL_METADATA_KEY)
    if not isinstance(route, str) or ":" not in route:
        return None
    channel, chat_id = route.split(":", 1)
    if not channel or not chat_id:
        return None
    return channel, chat_id


def parse_session_key(key: str) -> tuple[str, str, int]:
    """Parse a session key into ``(channel, chat_id, index)``.

    ``index=0`` means the default (no suffix) session.

    The key format is ``channel:chat_id`` with an optional ``:N`` suffix
    for multi-session support.  ``chat_id`` may contain any characters,
    including colons.  The index is only extracted when the key has at
    least three ``:``-separated parts and the last part is purely digits.
    """
    parts = key.split(":")
    if len(parts) < 2:
        return key, "", 0
    channel = parts[0]
    # If there are ≥3 parts and the last part is a pure number, treat it
    # as the session index.  This avoids misinterpreting ``weixin:123``
    # (where ``123`` is the chat_id) as having an index.
    if len(parts) >= 3 and parts[-1].isdigit():
        index = int(parts[-1])
        chat_id = ":".join(parts[1:-1])
    else:
        index = 0
        chat_id = ":".join(parts[1:])
    return channel, chat_id, index


def session_base_key(key: str) -> str:
    """Return the base key without the session index suffix.

    For ``weixin:12345@chatroom:2`` → ``weixin:12345@chatroom``.
    For ``weixin:12345@chatroom`` → ``weixin:12345@chatroom`` (unchanged).
    """
    parts = key.split(":")
    if len(parts) >= 3 and parts[-1].isdigit():
        return ":".join(parts[:-1])
    return key
