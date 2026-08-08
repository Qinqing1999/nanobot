"""Shared session key constants and helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

UNIFIED_SESSION_KEY = "unified:default"
LAST_CHANNEL_METADATA_KEY = "last_channel"

# Matches channel:chat_id with optional :index suffix.
_SESSION_SUFFIX_RE = re.compile(r"^(\w+:\w+)(?::(\d+))?$")


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
    For keys that don't match the standard pattern, returns the key as
    ``channel`` with an empty ``chat_id`` and ``index=0``.
    """
    m = _SESSION_SUFFIX_RE.match(key)
    if not m:
        if ":" in key:
            channel, _, chat_id = key.partition(":")
            return channel, chat_id, 0
        return key, "", 0
    channel_chat = m.group(1)
    index = int(m.group(2)) if m.group(2) else 0
    channel, _, chat_id = channel_chat.partition(":")
    return channel, chat_id, index


def session_base_key(key: str) -> str:
    """Return the base key without the session index suffix."""
    m = _SESSION_SUFFIX_RE.match(key)
    return m.group(1) if m else key
