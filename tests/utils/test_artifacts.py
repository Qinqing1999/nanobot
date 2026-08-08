from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nanobot.config.loader import set_config_path
from nanobot.utils.artifacts import (
    ArtifactError,
    decode_image_data_url,
    store_generated_image_artifact,
    store_generated_video_artifact,
)
from nanobot.utils.helpers import detect_file_mime, mime_to_artifact_type

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_decode_image_data_url_validates_image_payload() -> None:
    raw, mime = decode_image_data_url(PNG_DATA_URL)

    assert raw.startswith(b"\x89PNG")
    assert mime == "image/png"

    with pytest.raises(ArtifactError):
        decode_image_data_url("data:image/png;base64,not-base64")


def test_store_generated_image_artifact_writes_image_and_sidecar(tmp_path: Path) -> None:
    set_config_path(tmp_path / "config.json")
    created_at = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)

    artifact = store_generated_image_artifact(
        PNG_DATA_URL,
        prompt="draw a tiny pixel",
        model="openai/gpt-5.4-image-2",
        source_images=["/tmp/ref.png"],
        save_dir="generated",
        created_at=created_at,
    )

    image_path = Path(artifact["path"])
    assert image_path.is_file()
    assert image_path.parent == tmp_path / "media" / "generated" / "2026-05-08"
    assert artifact["id"].startswith("img_")
    assert artifact["mime"] == "image/png"

    sidecar = image_path.with_suffix(".json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata["path"] == str(image_path)
    assert metadata["source_images"] == ["/tmp/ref.png"]


def test_store_generated_image_artifact_rejects_unsafe_save_dir(tmp_path: Path) -> None:
    set_config_path(tmp_path / "config.json")

    with pytest.raises(ArtifactError):
        store_generated_image_artifact(
            PNG_DATA_URL,
            prompt="x",
            model="m",
            save_dir="../outside",
        )


def test_store_generated_video_artifact_writes_mp4_and_sidecar(tmp_path: Path) -> None:
    set_config_path(tmp_path / "config.json")
    created_at = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100  # fake mp4

    artifact = store_generated_video_artifact(
        video_bytes,
        prompt="a cat walking on the beach",
        model="agnes-video-v2.0",
        save_dir="generated",
        provider="agnes",
        created_at=created_at,
    )

    vid_path = Path(artifact["path"])
    assert vid_path.is_file()
    assert vid_path.parent == tmp_path / "media" / "generated" / "2026-05-08"
    assert artifact["id"].startswith("vid_")
    assert artifact["mime"] == "video/mp4"
    assert vid_path.read_bytes() == video_bytes

    sidecar = vid_path.with_suffix(".json")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata["prompt"] == "a cat walking on the beach"
    assert metadata["model"] == "agnes-video-v2.0"
    assert metadata["provider"] == "agnes"


def test_detect_file_mime_by_extension(tmp_path: Path) -> None:
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert detect_file_mime(f) == "application/pdf"

    f2 = tmp_path / "clip.mp4"
    f2.write_bytes(b"\x00" * 10)
    assert detect_file_mime(f2) == "video/mp4"


def test_detect_file_mime_fallback_to_octet_stream(tmp_path: Path) -> None:
    f = tmp_path / "unknown.zzzzz"
    f.write_bytes(b"\x00\x01\x02")
    assert detect_file_mime(f) == "application/octet-stream"


def test_mime_to_artifact_type() -> None:
    assert mime_to_artifact_type("image/png") == "image"
    assert mime_to_artifact_type("image/jpeg") == "image"
    assert mime_to_artifact_type("video/mp4") == "video"
    assert mime_to_artifact_type("video/webm") == "video"
    assert mime_to_artifact_type("audio/mpeg") == "audio"
    assert mime_to_artifact_type("application/pdf") == "document"
    assert mime_to_artifact_type(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ) == "document"
