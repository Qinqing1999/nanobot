"""Artifact persistence helpers for generated media."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from nanobot.config.paths import get_media_dir
from nanobot.utils.helpers import detect_image_mime, ensure_dir

_DATA_IMAGE_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

class ArtifactError(ValueError):
    """Raised when an artifact cannot be safely decoded or stored."""


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    """Decode a base64 image data URL and return ``(bytes, mime)``."""
    match = _DATA_IMAGE_RE.match(data_url.strip())
    if match is None:
        raise ArtifactError("expected a base64 image data URL")

    declared_mime, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ArtifactError("invalid base64 image payload") from exc

    detected_mime = detect_image_mime(raw)
    if detected_mime is None:
        raise ArtifactError("unsupported or unrecognized image data")
    if declared_mime != detected_mime:
        declared_mime = detected_mime
    return raw, declared_mime


def _safe_relative_dir(save_dir: str) -> Path:
    normalized = save_dir.replace("\\", "/").strip("/")
    if not normalized:
        raise ArtifactError("save_dir must not be empty")
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ArtifactError("save_dir must be a safe relative path")
    return Path(*rel.parts)


def _artifact_root(save_dir: str) -> Path:
    media_root = get_media_dir().resolve()
    root = (media_root / _safe_relative_dir(save_dir)).resolve()
    try:
        root.relative_to(media_root)
    except ValueError as exc:
        raise ArtifactError("artifact directory escapes media root") from exc
    return root


def store_generated_image_artifact(
    data_url: str,
    *,
    prompt: str,
    model: str,
    source_images: list[str] | None = None,
    save_dir: str = "generated",
    provider: str = "openrouter",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a generated image and sidecar metadata under the media root."""
    raw, mime = decode_image_data_url(data_url)
    ext = _MIME_EXTENSIONS.get(mime)
    if ext is None:
        raise ArtifactError(f"unsupported image MIME type: {mime}")

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(_artifact_root(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"img_{uuid.uuid4().hex[:12]}"
    image_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    image_path.write_bytes(raw)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(image_path),
        "mime": mime,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "source_images": list(source_images or []),
        "created_at": now.isoformat(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def generated_image_tool_result(artifacts: list[dict[str, Any]]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifacts": artifacts,
            "next_step": (
                "Use these artifact paths as reference_images for follow-up edits. "
                "Call the message tool with the artifact paths in the media parameter "
                "to deliver the images to the user. Keep raw paths internal unless the "
                "user asks for debug details."
            ),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Video artifacts
# ---------------------------------------------------------------------------

_VIDEO_MIME_EXTENSIONS: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def store_generated_video_artifact(
    video_bytes: bytes,
    *,
    prompt: str,
    model: str,
    save_dir: str = "generated",
    provider: str = "agnes",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist a generated video and sidecar metadata under the media root.

    Unlike :func:`store_generated_image_artifact`, this accepts raw bytes
    (downloaded from a provider-hosted URL) rather than a data URL, because
    video files are too large for base64 data URLs.
    """
    mime = "video/mp4"  # Agnes Video V2.0 returns MP4
    ext = _VIDEO_MIME_EXTENSIONS.get(mime, ".mp4")

    now = created_at or datetime.now().astimezone()
    day_dir = ensure_dir(_artifact_root(save_dir) / now.strftime("%Y-%m-%d"))
    artifact_id = f"vid_{uuid.uuid4().hex[:12]}"
    video_path = day_dir / f"{artifact_id}{ext}"
    metadata_path = day_dir / f"{artifact_id}.json"

    video_path.write_bytes(video_bytes)
    metadata: dict[str, Any] = {
        "id": artifact_id,
        "path": str(video_path),
        "mime": mime,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "created_at": now.isoformat(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def generated_video_tool_result(artifact: dict[str, Any]) -> str:
    """Return the compact structured result exposed to the LLM."""
    return json.dumps(
        {
            "artifact": artifact,
            "next_step": (
                "视频已生成。使用 message 工具发送 artifact 的 path 给用户。"
                "生成的视频已注册到制品注册表，可通过制品 ID 在后续对话中引用。"
            ),
        },
        ensure_ascii=False,
    )
