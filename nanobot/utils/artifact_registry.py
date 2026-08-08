"""Session-level artifact registry with four-digit numeric IDs."""

from __future__ import annotations

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
