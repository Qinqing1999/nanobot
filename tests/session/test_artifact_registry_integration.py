"""Tests for artifact registry integration with Session."""

from __future__ import annotations

from nanobot.session.manager import Session


def test_session_artifact_registry_lazy_init() -> None:
    session = Session(key="test:chat")
    reg = session.artifact_registry
    assert reg is not None
    assert reg.list_all() == []


def test_session_artifact_registry_persist_and_restore() -> None:
    session = Session(key="test:chat")
    reg = session.artifact_registry
    reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg", source="upload")
    reg.register(type="video", path="/tmp/v.mp4", mime="video/mp4", source="generated")
    session.sync_artifact_registry()

    # Verify metadata now contains serialized registry
    assert "artifact_registry" in session.metadata

    # Simulate a new session loading from metadata
    session2 = Session(key="test:chat", metadata=dict(session.metadata))
    reg2 = session2.artifact_registry
    entries = reg2.list_all()
    assert len(entries) == 2
    assert entries[0].id == "1001"
    assert entries[1].id == "2001"


def test_session_clear_resets_artifact_registry() -> None:
    session = Session(key="test:chat")
    reg = session.artifact_registry
    reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg")
    session.sync_artifact_registry()
    assert "artifact_registry" in session.metadata

    session.clear()

    assert "artifact_registry" not in session.metadata
    reg2 = session.artifact_registry
    assert reg2.list_all() == []


def test_session_can_continue_registering_after_restore() -> None:
    session = Session(key="test:chat")
    reg = session.artifact_registry
    reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg")
    session.sync_artifact_registry()

    session2 = Session(key="test:chat", metadata=dict(session.metadata))
    reg2 = session2.artifact_registry
    # Next ID should continue from 1002
    entry = reg2.register(type="image", path="/tmp/b.jpg", mime="image/jpeg")
    assert entry.id == "1002"
