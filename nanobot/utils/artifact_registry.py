"""Session-level artifact registry with four-digit numeric IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ID 前缀: 0=文档, 1=图片, 2=视频, 3=音频
TYPE_PREFIXES: dict[str, int] = {
    "document": 0,  # PPT, Word, PDF, etc.
    "image": 1,
    "video": 2,
    "audio": 3,
}
TYPE_FROM_PREFIX: dict[int, str] = {v: k for k, v in TYPE_PREFIXES.items()}

# 中文类型标签
TYPE_LABELS_CN: dict[str, str] = {
    "document": "文档",
    "image": "图片",
    "video": "视频",
    "audio": "音频",
}


@dataclass
class ArtifactEntry:
    """A single registered artifact with its metadata."""

    id: str              # 四位数字, 如 "1001"
    type: str            # "document" | "image" | "video" | "audio"
    path: str            # 本地文件路径
    filename: str        # 原始文件名
    mime: str            # MIME 类型
    source: str           # "upload" | "generated"
    prompt: str | None   # 生成时的 prompt（如有）
    model: str | None    # 生成模型（如有）
    created_at: str       # ISO timestamp


class ArtifactRegistry:
    """Per-session artifact ID → entry mapping.

    IDs are four-digit numeric strings where the first digit is a type prefix:
    0=document, 1=image, 2=video, 3=audio.  Each type has an independent
    counter that increments within the session.

    The registry is serialisable via :meth:`to_dict` / :meth:`from_dict` so it
    can be persisted to and restored from session metadata.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ArtifactEntry] = {}
        self._counters: dict[str, int] = {}  # type → next number

    def register(
        self,
        *,
        type: str,
        path: str,
        filename: str = "",
        mime: str = "",
        source: str = "upload",
        prompt: str | None = None,
        model: str | None = None,
    ) -> ArtifactEntry:
        """Register a new artifact and return its entry."""
        prefix = TYPE_PREFIXES.get(type)
        if prefix is None:
            raise ValueError(f"Unknown artifact type: {type}")
        num = self._counters.get(type, 0) + 1
        self._counters[type] = num
        artifact_id = f"{prefix}{num:03d}"  # e.g. "1001", "0001", "2001"
        entry = ArtifactEntry(
            id=artifact_id,
            type=type,
            path=path,
            filename=filename or Path(path).name,
            mime=mime,
            source=source,
            prompt=prompt,
            model=model,
            created_at=datetime.now().astimezone().isoformat(),
        )
        self._entries[artifact_id] = entry
        return entry

    def get(self, artifact_id: str) -> ArtifactEntry | None:
        """Return the entry for *artifact_id*, or None."""
        return self._entries.get(artifact_id)

    def find_by_path(self, path: str) -> ArtifactEntry | None:
        """Return the first entry whose path matches, or None."""
        target = str(path)
        for entry in self._entries.values():
            if entry.path == target:
                return entry
        return None

    def resolve_path(self, artifact_id: str) -> str | None:
        """Return the local file path for *artifact_id*, or None."""
        entry = self._entries.get(artifact_id)
        return entry.path if entry else None

    def list_all(self) -> list[ArtifactEntry]:
        """Return all entries sorted by ID."""
        return sorted(self._entries.values(), key=lambda e: e.id)

    def list_by_type(self, type: str) -> list[ArtifactEntry]:
        """Return entries of a specific type, sorted by ID."""
        return [e for e in self._entries.values() if e.type == type]

    def to_context_lines(self) -> list[str]:
        """Format the registry for injection into agent runtime context."""
        if not self._entries:
            return []
        lines = ["[已注册的制品]"]
        for entry in self._entries.values():
            desc = entry.prompt or entry.filename or Path(entry.path).name
            lines.append(f"  {entry.id} [{entry.type}] {desc}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry for persistence."""
        return {
            "entries": {k: v.__dict__ for k, v in self._entries.items()},
            "counters": dict(self._counters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRegistry:
        """Deserialise a registry from persisted data."""
        reg = cls()
        for k, v in data.get("entries", {}).items():
            reg._entries[k] = ArtifactEntry(**v)
        reg._counters = dict(data.get("counters", {}))
        return reg


def format_artifact_notification(artifacts: list[dict[str, str]]) -> str:
    """Format artifact registration info as a user-facing notification message.

    Uses markdown formatting: ``**KEY**: VALUE``

    Args:
        artifacts: List of dicts with keys ``id``, ``type``, ``filename``.

    Returns:
        A multi-line string like::

            📎 已注册 2 个制品:
              **制品 ID**: `1001` | **类型**: `图片` | **文件名**: `cat.png`
              **制品 ID**: `1002` | **类型**: `图片` | **文件名**: `dog.png`
    """
    if not artifacts:
        return ""
    count = len(artifacts)
    header = f"📎 已注册 {count} 个制品:"
    lines = [header]
    for a in artifacts:
        art_id = a.get("id", "????")
        art_type = a.get("type", "document")
        label = TYPE_LABELS_CN.get(art_type, art_type)
        filename = a.get("filename", "")
        lines.append(
            f"  **制品 ID**: `{art_id}` | **类型**: `{label}` | **文件名**: `{filename}`"
        )
    return "\n".join(lines)


# Regex for 4-digit artifact ID candidates in user text.
_ARTIFACT_ID_RE = re.compile(r"\b(\d{4})\b")

# Extensions that write_file should register as artifacts.
# Code files (.py, .js, .ts, .go, .rs, …) are excluded to avoid registry noise.
_REGISTERABLE_EXTENSIONS: dict[str, str] = {
    # Documents
    ".md": "document", ".txt": "document", ".rst": "document",
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".xls": "document", ".xlsx": "document", ".ppt": "document", ".pptx": "document",
    ".csv": "document", ".json": "document", ".html": "document", ".htm": "document",
    ".rtf": "document", ".odt": "document", ".ods": "document", ".odp": "document",
    ".yaml": "document", ".yml": "document", ".toml": "document", ".ini": "document",
    # Images
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".svg": "image",
    # Audio
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio", ".m4a": "audio",
    # Video
    ".mp4": "video", ".webm": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
}


def artifact_type_for_extension(suffix: str) -> str | None:
    """Return the artifact type for a file extension, or None if not registerable."""
    return _REGISTERABLE_EXTENSIONS.get(suffix.lower())


def resolve_artifact_references(
    text: str,
    registry: ArtifactRegistry,
    existing_media: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Resolve artifact ID references in user text.

    Scans *text* for 4-digit numbers, looks each up in *registry*,
    and for hits:
      - Adds the artifact's file path to the returned media list
        (skipping paths already in *existing_media*)
      - Replaces the bare ID in text with ``[制品 1001: 图片, cat.png]``

    IDs not in the registry are left untouched.

    Returns ``(modified_text, new_media_paths)``.
    """
    if not text or not registry._entries:
        return text, []

    existing_set = set(existing_media or [])
    new_media: list[str] = []
    resolved: dict[str, ArtifactEntry] = {}

    for match in _ARTIFACT_ID_RE.finditer(text):
        candidate = match.group(1)
        if candidate in resolved:
            continue
        entry = registry.get(candidate)
        if entry is not None:
            resolved[candidate] = entry

    if not resolved:
        return text, []

    def _replace_ref(m: re.Match[str]) -> str:
        aid = m.group(1)
        entry = resolved.get(aid)
        if entry is None:
            return aid
        label = TYPE_LABELS_CN.get(entry.type, entry.type)
        filename = entry.filename or Path(entry.path).name
        return f"[制品 {aid}: {label}, {filename}]"

    modified = _ARTIFACT_ID_RE.sub(_replace_ref, text)

    for aid, entry in resolved.items():
        if entry.path and entry.path not in existing_set and entry.path not in new_media:
            new_media.append(entry.path)

    return modified, new_media
