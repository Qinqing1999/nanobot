"""Shared image resolution helpers for image-related tools.

Provides artifact ID and file path resolution for tools that accept
image inputs (segment_subject, apply_mask, generate_image, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_access import current_tool_workspace
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from nanobot.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


class ImageInputError(ValueError):
    """Raised when an image input cannot be resolved."""


def resolve_image_input(
    value: str,
    workspace: str | Path,
    sessions: SessionManager | None = None,
) -> Path:
    """Resolve an image input to a local file path.

    Accepts either a four-digit artifact ID (e.g. "1021") or a file path.
    For artifact IDs, looks up the session's Artifact Registry.
    For file paths, validates against the workspace and media directory boundaries.
    """
    if not value or not isinstance(value, str):
        raise ImageInputError("image parameter must be a non-empty string")

    # Try artifact ID first (pure digits, 1-4 chars)
    stripped = value.strip()
    if stripped.isdigit():
        path = _resolve_artifact_id(stripped, sessions)
        if path is not None:
            return path
        # Fall through to file path resolution

    return _resolve_file_path(stripped, workspace)


def _resolve_artifact_id(
    artifact_id: str,
    sessions: SessionManager | None,
) -> Path | None:
    """Look up an artifact ID in the current session's registry."""
    from nanobot.agent.tools.context import current_request_context

    ctx = current_request_context()
    if ctx is None or not ctx.session_key or sessions is None:
        return None

    try:
        session = sessions.get_or_create(ctx.session_key)
    except Exception:
        return None

    entry = session.artifact_registry.get(artifact_id)
    if entry is None:
        return None

    path = Path(entry.path)
    if not path.is_file():
        return None

    return path


def _resolve_file_path(value: str, workspace: str | Path) -> Path:
    """Resolve and validate a file path against workspace boundaries."""
    access = current_tool_workspace(workspace, restrict_to_workspace=True)
    ws = access.project_path or Path(workspace)

    try:
        resolved = resolve_allowed_path(
            value,
            workspace=ws,
            allowed_root=access.allowed_root,
            extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
            strict=True,
        )
    except WorkspaceBoundaryError as exc:
        raise ImageInputError(
            "image path must be inside the workspace or nanobot media directory"
        ) from exc
    except OSError as exc:
        raise ImageInputError(f"image file not found: {value}") from exc

    if not resolved.is_file():
        raise ImageInputError(f"image is not a file: {value}")

    raw = resolved.read_bytes()
    if detect_image_mime(raw) is None:
        raise ImageInputError(f"unsupported image format: {value}")

    return resolved


def resolve_mask_path(value: str, workspace: str | Path) -> Path:
    """Resolve a mask file path (masks are never artifact IDs).

    Validates against the workspace and media directory boundaries,
    and confirms the file is a valid image.
    """
    if not value or not isinstance(value, str):
        raise ImageInputError("mask parameter must be a non-empty file path")

    access = current_tool_workspace(workspace, restrict_to_workspace=True)
    ws = access.project_path or Path(workspace)

    try:
        resolved = resolve_allowed_path(
            value,
            workspace=ws,
            allowed_root=access.allowed_root,
            extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
            strict=True,
        )
    except WorkspaceBoundaryError as exc:
        raise ImageInputError(
            "mask path must be inside the workspace or nanobot media directory"
        ) from exc
    except OSError as exc:
        raise ImageInputError(f"mask file not found: {value}") from exc

    if not resolved.is_file():
        raise ImageInputError(f"mask is not a file: {value}")

    raw = resolved.read_bytes()
    if detect_image_mime(raw) is None:
        raise ImageInputError(f"mask file is not a valid image: {value}")

    return resolved
