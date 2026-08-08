"""Tests for the session-level ArtifactRegistry."""

from __future__ import annotations

from nanobot.utils.artifact_registry import (
    TYPE_PREFIXES,
    ArtifactEntry,
    ArtifactRegistry,
)


def test_register_upload_document_gets_zero_prefix() -> None:
    reg = ArtifactRegistry()
    entry = reg.register(
        type="document",
        path="/tmp/report.pdf",
        filename="report.pdf",
        mime="application/pdf",
        source="upload",
    )
    assert entry.id == "0001"
    assert entry.type == "document"
    assert entry.path == "/tmp/report.pdf"
    assert entry.source == "upload"


def test_register_upload_image_gets_one_prefix() -> None:
    reg = ArtifactRegistry()
    entry = reg.register(
        type="image",
        path="/tmp/photo.jpg",
        filename="photo.jpg",
        mime="image/jpeg",
        source="upload",
    )
    assert entry.id == "1001"
    assert entry.type == "image"


def test_register_generated_video_gets_two_prefix() -> None:
    reg = ArtifactRegistry()
    entry = reg.register(
        type="video",
        path="/tmp/clip.mp4",
        filename="clip.mp4",
        mime="video/mp4",
        source="generated",
        prompt="a cat on the beach",
        model="agnes-video-v2.0",
    )
    assert entry.id == "2001"
    assert entry.type == "video"
    assert entry.prompt == "a cat on the beach"
    assert entry.model == "agnes-video-v2.0"


def test_id_uniqueness_same_type_increments() -> None:
    reg = ArtifactRegistry()
    e1 = reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg")
    e2 = reg.register(type="image", path="/tmp/b.jpg", mime="image/jpeg")
    e3 = reg.register(type="image", path="/tmp/c.jpg", mime="image/jpeg")
    assert e1.id == "1001"
    assert e2.id == "1002"
    assert e3.id == "1003"


def test_id_different_types_independent_counters() -> None:
    reg = ArtifactRegistry()
    doc1 = reg.register(type="document", path="/tmp/a.pdf", mime="application/pdf")
    img1 = reg.register(type="image", path="/tmp/b.jpg", mime="image/jpeg")
    doc2 = reg.register(type="document", path="/tmp/c.pdf", mime="application/pdf")
    vid1 = reg.register(type="video", path="/tmp/d.mp4", mime="video/mp4")
    img2 = reg.register(type="image", path="/tmp/e.jpg", mime="image/jpeg")

    assert doc1.id == "0001"
    assert img1.id == "1001"
    assert doc2.id == "0002"
    assert vid1.id == "2001"
    assert img2.id == "1002"


def test_get_by_id() -> None:
    reg = ArtifactRegistry()
    reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg")
    entry = reg.get("1001")
    assert entry is not None
    assert entry.path == "/tmp/a.jpg"

    assert reg.get("9999") is None


def test_resolve_path() -> None:
    reg = ArtifactRegistry()
    reg.register(type="video", path="/tmp/clip.mp4", mime="video/mp4")
    assert reg.resolve_path("2001") == "/tmp/clip.mp4"
    assert reg.resolve_path("9999") is None


def test_list_all_sorted_by_id() -> None:
    reg = ArtifactRegistry()
    reg.register(type="video", path="/tmp/v.mp4", mime="video/mp4")
    reg.register(type="document", path="/tmp/d.pdf", mime="application/pdf")
    reg.register(type="image", path="/tmp/i.jpg", mime="image/jpeg")

    entries = reg.list_all()
    ids = [e.id for e in entries]
    assert ids == ["0001", "1001", "2001"]


def test_list_by_type() -> None:
    reg = ArtifactRegistry()
    reg.register(type="image", path="/tmp/a.jpg", mime="image/jpeg")
    reg.register(type="document", path="/tmp/d.pdf", mime="application/pdf")
    reg.register(type="image", path="/tmp/b.jpg", mime="image/jpeg")

    images = reg.list_by_type("image")
    assert len(images) == 2
    assert images[0].id == "1001"
    assert images[1].id == "1002"


def test_to_context_lines_empty() -> None:
    reg = ArtifactRegistry()
    assert reg.to_context_lines() == []


def test_to_context_lines_with_entries() -> None:
    reg = ArtifactRegistry()
    reg.register(
        type="image", path="/tmp/a.jpg", filename="sunset.jpg",
        mime="image/jpeg", source="upload",
    )
    reg.register(
        type="video", path="/tmp/v.mp4", filename="clip.mp4",
        mime="video/mp4", source="generated", prompt="a cat walking",
    )

    lines = reg.to_context_lines()
    assert len(lines) == 3  # header + 2 entries
    assert "已注册" in lines[0] or "artifact" in lines[0].lower()
    assert "1001" in lines[1]
    assert "2001" in lines[2]


def test_to_dict_from_dict_roundtrip() -> None:
    reg = ArtifactRegistry()
    reg.register(type="image", path="/tmp/a.jpg", filename="a.jpg", mime="image/jpeg", source="upload")
    reg.register(type="video", path="/tmp/v.mp4", filename="v.mp4", mime="video/mp4", source="generated", prompt="test")

    data = reg.to_dict()
    reg2 = ArtifactRegistry.from_dict(data)

    assert reg2.get("1001") is not None
    assert reg2.get("2001") is not None
    assert reg2.get("1001").path == "/tmp/a.jpg"
    assert reg2.get("2001").prompt == "test"

    # Can continue registering after deserialization
    entry = reg2.register(type="image", path="/tmp/b.jpg", mime="image/jpeg")
    assert entry.id == "1002"


def test_register_unknown_type_raises() -> None:
    reg = ArtifactRegistry()
    import pytest

    with pytest.raises(ValueError, match="Unknown artifact type"):
        reg.register(type="unknown", path="/tmp/x", mime="")


def test_type_prefixes_complete() -> None:
    assert TYPE_PREFIXES["document"] == 0
    assert TYPE_PREFIXES["image"] == 1
    assert TYPE_PREFIXES["video"] == 2
    assert TYPE_PREFIXES["audio"] == 3
