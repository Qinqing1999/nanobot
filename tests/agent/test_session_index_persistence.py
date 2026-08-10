"""Tests for session index tracker persistence (ADR-0011).

Covers:
- _load_session_indices() loads from session_indices.json
- _load_session_indices() returns empty dict when file missing
- _flush_session_indices() writes current _session_indices to disk
- After restart (new AgentLoop), _session_indices is restored from file
- After restart, regular messages route to the highest index session
- After restart, /new creates max+1 (not from 0)
- Only session 0 -> no change after restart
- Session file deleted -> lazy fallback to next highest
- Multiple channels do not interfere
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot.session.keys import session_key_for_channel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loop(tmp_path: Path) -> AgentLoop:
    """Create a minimal AgentLoop with a real SessionManager."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.estimate_prompt_tokens.return_value = (10_000, "test")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    provider.generation.max_tokens = 4096
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


def _indices_file_path(loop: AgentLoop) -> Path:
    """Return the path where session_indices.json should live."""
    return loop.sessions.sessions_dir / "session_indices.json"


# ---------------------------------------------------------------------------
# Low-level: _load / _flush
# ---------------------------------------------------------------------------

class TestLoadSessionIndices:
    """Direct tests for loading session_indices.json at init."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        assert loop._session_indices == {}

    def test_loads_from_file(self, tmp_path: Path) -> None:
        """When session_indices.json exists, __init__ loads it."""
        workspace = tmp_path
        sessions_dir = workspace / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "session_indices.json").write_text(
            json.dumps({"weixin:user1": 3, "telegram:42": 1})
        )
        loop = _make_loop(workspace)
        assert loop._session_indices == {"weixin:user1": 3, "telegram:42": 1}

    def test_corrupt_file_falls_back_to_empty(self, tmp_path: Path) -> None:
        """A corrupt session_indices.json should not crash; fall back to empty."""
        workspace = tmp_path
        sessions_dir = workspace / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "session_indices.json").write_text("not valid json{")
        loop = _make_loop(workspace)
        assert loop._session_indices == {}


class TestFlushSessionIndices:
    """Direct tests for _flush_session_indices()."""

    def test_writes_current_indices_to_disk(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._session_indices = {"cli:test": 2, "weixin:user": 5}
        loop._flush_session_indices()
        path = _indices_file_path(loop)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {"cli:test": 2, "weixin:user": 5}

    def test_flush_then_load_roundtrip(self, tmp_path: Path) -> None:
        """Flush then create new loop -> indices restored."""
        loop1 = _make_loop(tmp_path)
        loop1._session_indices = {"cli:test": 3}
        loop1._flush_session_indices()
        loop2 = _make_loop(tmp_path)
        assert loop2._session_indices == {"cli:test": 3}

    def test_empty_dict_flushes_empty_file(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop._flush_session_indices()
        path = _indices_file_path(loop)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {}


# ---------------------------------------------------------------------------
# High-level: AgentLoop behavior across simulated restart
# ---------------------------------------------------------------------------

class TestRestartRouting:
    """End-to-end: message routing after simulated restart."""

    @pytest.mark.asyncio
    async def test_message_routes_to_highest_index_after_restart(
        self, tmp_path: Path
    ) -> None:
        """After restart, a regular message routes to the highest index session."""
        loop1 = _make_loop(tmp_path)
        base_key = "cli:test"

        # Simulate having created sessions 1, 2, 3 via /new
        for idx in range(1, 4):
            key = session_key_for_channel("cli", "test", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            session.add_message("user", f"msg in session {idx}")
            loop1.sessions.save(session)
        loop1._session_indices[base_key] = 3
        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Simulate restart
        loop2 = _make_loop(tmp_path)
        assert loop2._session_indices.get(base_key) == 3

        msg = InboundMessage(channel="cli", sender_id="u", chat_id="test", content="hello")
        effective_key = loop2._effective_session_key(msg)
        assert effective_key == "cli:test:3"

    @pytest.mark.asyncio
    async def test_new_creates_max_plus_one_after_restart(
        self, tmp_path: Path
    ) -> None:
        """After restart, /new should create session max+1, not from 1."""
        loop1 = _make_loop(tmp_path)
        base_key = "cli:test"

        # Create sessions 1, 2, 3
        for idx in range(1, 4):
            key = session_key_for_channel("cli", "test", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            loop1.sessions.save(session)
        loop1._session_indices[base_key] = 3
        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Simulate restart
        loop2 = _make_loop(tmp_path)

        # Execute /new via process_direct
        result = await loop2.process_direct(
            "/new", session_key="cli:test:3", channel="cli", chat_id="test"
        )
        assert result is not None
        assert "session 4" in result.content
        assert loop2._session_indices[base_key] == 4

    @pytest.mark.asyncio
    async def test_only_session_zero_no_change_after_restart(
        self, tmp_path: Path
    ) -> None:
        """When only session 0 exists, restart keeps routing to session 0."""
        loop1 = _make_loop(tmp_path)

        # Only session 0 (default) exists, no /new ever called
        session = loop1.sessions.get_or_create("cli:test")
        session.add_message("user", "hello")
        loop1.sessions.save(session)
        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Simulate restart
        loop2 = _make_loop(tmp_path)

        msg = InboundMessage(channel="cli", sender_id="u", chat_id="test", content="hi")
        effective_key = loop2._effective_session_key(msg)
        assert effective_key == "cli:test"

    @pytest.mark.asyncio
    async def test_multiple_channels_independent(self, tmp_path: Path) -> None:
        """Different channels' session indices should not interfere."""
        loop1 = _make_loop(tmp_path)

        # Channel A: weixin:user1 -> index 2
        for idx in range(1, 3):
            key = session_key_for_channel("weixin", "user1", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            loop1.sessions.save(session)
        loop1._session_indices["weixin:user1"] = 2

        # Channel B: telegram:42 -> index 5
        for idx in range(1, 6):
            key = session_key_for_channel("telegram", "42", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            loop1.sessions.save(session)
        loop1._session_indices["telegram:42"] = 5

        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Simulate restart
        loop2 = _make_loop(tmp_path)
        assert loop2._session_indices.get("weixin:user1") == 2
        assert loop2._session_indices.get("telegram:42") == 5

        # weixin message routes to :2
        msg_wx = InboundMessage(channel="weixin", sender_id="u", chat_id="user1", content="hi")
        assert loop2._effective_session_key(msg_wx) == "weixin:user1:2"

        # telegram message routes to :5
        msg_tg = InboundMessage(channel="telegram", sender_id="u", chat_id="42", content="hi")
        assert loop2._effective_session_key(msg_tg) == "telegram:42:5"


class TestLazyDeletionCheck:
    """When the target session file is deleted, fall back to next highest."""

    @pytest.mark.asyncio
    async def test_deleted_highest_falls_back_to_next(self, tmp_path: Path) -> None:
        """If session 3 is deleted, routing falls back to session 2."""
        loop1 = _make_loop(tmp_path)
        base_key = "cli:test"

        # Create sessions 1, 2, 3
        for idx in range(1, 4):
            key = session_key_for_channel("cli", "test", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            session.add_message("user", f"msg {idx}")
            loop1.sessions.save(session)
        loop1._session_indices[base_key] = 3
        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Delete session 3 file
        loop1.sessions.delete_session("cli:test:3")

        # Simulate restart
        loop2 = _make_loop(tmp_path)
        assert loop2._session_indices.get(base_key) == 3  # loaded from file

        # Message should fall back to session 2 (highest existing)
        msg = InboundMessage(channel="cli", sender_id="u", chat_id="test", content="hello")
        effective_key = loop2._effective_session_key(msg)
        assert effective_key == "cli:test:2"

        # _session_indices should be updated
        assert loop2._session_indices[base_key] == 2

    @pytest.mark.asyncio
    async def test_all_indexed_sessions_deleted_falls_back_to_zero(
        self, tmp_path: Path
    ) -> None:
        """If all indexed sessions are deleted, fall back to session 0."""
        loop1 = _make_loop(tmp_path)
        base_key = "cli:test"

        for idx in range(1, 3):
            key = session_key_for_channel("cli", "test", session_index=idx)
            session = loop1.sessions.get_or_create(key)
            loop1.sessions.save(session)
        loop1._session_indices[base_key] = 2
        loop1._flush_session_indices()
        await loop1.close_mcp()

        # Delete all indexed sessions
        loop1.sessions.delete_session("cli:test:1")
        loop1.sessions.delete_session("cli:test:2")

        # Simulate restart
        loop2 = _make_loop(tmp_path)

        msg = InboundMessage(channel="cli", sender_id="u", chat_id="test", content="hello")
        effective_key = loop2._effective_session_key(msg)
        assert effective_key == "cli:test"
