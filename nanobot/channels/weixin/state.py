"""WeChat-owned persisted login-state detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.channels.contracts import channel_field_value
from nanobot.config.paths import get_config_path


def _state_dir_for_instance(
    *,
    configured_dir: str | None,
    instance_id: str,
) -> Path:
    """Return the state directory for a specific WeChat instance."""
    if configured_dir:
        return Path(str(configured_dir)).expanduser()
    base = get_config_path().parent / "weixin"
    if instance_id and instance_id != "default":
        return base / instance_id
    return base


def _check_state_dir(state_dir: Path) -> bool:
    try:
        payload = json.loads((state_dir / "account.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return bool(str(payload.get("token") or "").strip())


def local_state_present(section: Any) -> bool:
    """Check if any WeChat instance has saved login state.

    Supports both legacy flat config and multi-instance config.
    """
    # Handle multi-instance config shape
    if isinstance(section, dict) and isinstance(section.get("instances"), list):
        for instance in section["instances"]:
            if not isinstance(instance, dict):
                continue
            instance_id = str(
                instance.get("id")
                or instance.get("instanceId")
                or instance.get("instance_id")
                or "default"
            )
            configured_dir = instance.get("stateDir") or instance.get("state_dir")
            state_dir = _state_dir_for_instance(
                configured_dir=str(configured_dir) if configured_dir else None,
                instance_id=instance_id,
            )
            if _check_state_dir(state_dir):
                return True
        return False

    # Legacy flat config (single instance)
    configured_dir = channel_field_value(section, "stateDir")
    state_dir = _state_dir_for_instance(
        configured_dir=str(configured_dir) if configured_dir else None,
        instance_id="default",
    )
    return _check_state_dir(state_dir)


__all__ = ["_state_dir_for_instance", "local_state_present"]
