"""WeChat-owned helpers for its persisted multi-instance configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.channels.contracts import ChannelInstanceSpec, ChannelManagementSpec
from nanobot.channels.weixin.state import _state_dir_for_instance, local_state_present
from nanobot.config.loader import merge_missing_defaults

DEFAULT_INSTANCE_ID = "default"
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_instance_id(value: str) -> str:
    """Return a normalized instance id or raise ValueError."""
    instance_id = value.strip()
    if not instance_id or not _INSTANCE_ID_RE.fullmatch(instance_id):
        raise ValueError("instance id must match [A-Za-z0-9_-]+")
    return instance_id


def runtime_channel_name(base_name: str, instance_id: str) -> str:
    """Return the channel key used for routing messages at runtime."""
    return base_name if instance_id == DEFAULT_INSTANCE_ID else f"{base_name}.{instance_id}"


def weixin_default_config() -> dict[str, Any]:
    """Return the default WeChat instance config as a plain dict."""
    from nanobot.channels.weixin.runtime import WeixinConfig

    return WeixinConfig().model_dump(by_alias=True)


def _base_weixin_instance_config(defaults: dict[str, Any]) -> dict[str, Any]:
    config = dict(defaults)
    config["instanceId"] = DEFAULT_INSTANCE_ID
    config["name"] = "nanobot"
    return config


def _normalize_weixin_instance(
    raw: dict[str, Any],
    defaults: dict[str, Any],
    *,
    inherited: dict[str, Any] | None = None,
    fallback_id: str = DEFAULT_INSTANCE_ID,
) -> dict[str, Any]:
    config = cast(dict[str, Any], merge_missing_defaults(inherited or {}, defaults))
    config = cast(dict[str, Any], merge_missing_defaults(raw, config))

    raw_id = raw.get("id") or raw.get("instanceId") or raw.get("instance_id") or fallback_id
    instance_id = validate_instance_id(str(raw_id))
    config["id"] = instance_id
    config["instanceId"] = instance_id
    config.setdefault(
        "name",
        "nanobot" if instance_id == DEFAULT_INSTANCE_ID else f"nanobot {instance_id}",
    )
    return config


def _weixin_instance_inputs(
    section: Any,
    defaults: dict[str, Any],
) -> tuple[list[Any], dict[str, Any] | None]:
    """Split a config section into instance list and inherited values."""
    if hasattr(section, "model_dump"):
        section = section.model_dump(mode="json", by_alias=True)
    if not isinstance(section, dict):
        section = {}
    section_data = cast(dict[str, Any], section)

    instances = section_data.get("instances")
    if isinstance(instances, list):
        inherited = {key: value for key, value in section_data.items() if key != "instances"}
        return list(cast(list[Any], instances)), inherited
    return ([section_data] if section_data else [_base_weixin_instance_config(defaults)]), None


def weixin_instance_specs(
    section: Any,
    defaults: dict[str, Any],
    *,
    enabled_only: bool = False,
) -> list[ChannelInstanceSpec]:
    """Expand legacy or canonical WeChat config into runtime instance specs."""
    raw_specs, inherited = _weixin_instance_inputs(section, defaults)

    specs: list[ChannelInstanceSpec] = []
    instance_ids: set[str] = set()
    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            logger.warning("Skipping invalid WeChat instance at index {}: expected an object", index)
            continue
        fallback_id = DEFAULT_INSTANCE_ID if index == 0 else f"assistant-{index + 1}"
        try:
            config = _normalize_weixin_instance(
                cast(dict[str, Any], raw),
                defaults,
                inherited=inherited,
                fallback_id=fallback_id,
            )
        except ValueError as exc:
            logger.warning("Skipping invalid WeChat instance config: {}", exc)
            continue

        instance_id = str(config["instanceId"])
        if instance_id in instance_ids:
            logger.warning("Skipping duplicate WeChat instance id '{}'", instance_id)
            continue

        instance_ids.add(instance_id)
        enabled = bool(config.get("enabled", defaults.get("enabled", False)))
        if enabled_only and not enabled:
            continue

        specs.append(
            ChannelInstanceSpec(
                instance_id=instance_id,
                config=config,
            )
        )

    return specs


def canonical_weixin_section(section: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical section, rejecting input that cannot be preserved safely."""
    raw_specs, inherited = _weixin_instance_inputs(section, defaults)
    instances: list[dict[str, Any]] = []
    instance_ids: set[str] = set()

    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            raise ValueError(f"WeChat instance at index {index} must be an object")
        fallback_id = DEFAULT_INSTANCE_ID if index == 0 else f"assistant-{index + 1}"
        try:
            config = _normalize_weixin_instance(
                cast(dict[str, Any], raw),
                defaults,
                inherited=inherited,
                fallback_id=fallback_id,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid WeChat instance at index {index}: {exc}") from exc

        instance_id = str(config["instanceId"])
        if instance_id in instance_ids:
            raise ValueError(f"duplicate WeChat instance id '{instance_id}'")
        instance_ids.add(instance_id)
        instances.append(config)

    return {"instances": instances}


def upsert_weixin_instance(
    section: Any,
    defaults: dict[str, Any],
    instance_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Return canonical WeChat section with one instance created or updated."""
    instance_id = validate_instance_id(instance_id)
    canonical = canonical_weixin_section(section, defaults)
    instances = canonical.setdefault("instances", [])

    for instance in instances:
        if instance.get("id") == instance_id or instance.get("instanceId") == instance_id:
            instance.update(values)
            instance["id"] = instance_id
            instance["instanceId"] = instance_id
            instance.setdefault(
                "name",
                "nanobot" if instance_id == DEFAULT_INSTANCE_ID else f"nanobot {instance_id}",
            )
            return canonical

    config = _normalize_weixin_instance(
        {**values, "id": instance_id},
        defaults,
        fallback_id=instance_id,
    )
    instances.append(config)
    return canonical


def managed_weixin_instance_specs(
    section: Any,
    *,
    enabled_only: bool = True,
) -> list[ChannelInstanceSpec]:
    return weixin_instance_specs(
        section,
        weixin_default_config(),
        enabled_only=enabled_only,
    )


def update_managed_weixin_instance(
    section: Any,
    values: dict[str, Any],
    *,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> dict[str, Any]:
    existing = cast(dict[str, Any], section) if isinstance(section, dict) else {}
    return upsert_weixin_instance(
        existing,
        weixin_default_config(),
        instance_id,
        values,
    )


def managed_weixin_feature_instances(
    section: Any,
    *,
    setup_spec: Any | None = None,
) -> list[dict[str, Any]] | None:
    """Return presentation overrides including the saved WeChat identity per instance."""
    defaults = weixin_default_config()
    specs = weixin_instance_specs(section, defaults, enabled_only=False)
    overrides: list[dict[str, Any]] = []
    for spec in specs:
        instance_id = spec.instance_id
        config = spec.config if isinstance(spec.config, dict) else {}
        configured_dir = config.get("stateDir") or config.get("state_dir")
        state_dir = _state_dir_for_instance(
            configured_dir=str(configured_dir) if configured_dir else None,
            instance_id=instance_id,
        )
        account_info = _read_account_info(state_dir)
        display_name = config.get("name") or instance_id
        if account_info["nickname"]:
            display_name = account_info["nickname"]
        elif account_info["ilink_user_id"]:
            display_name = f'{display_name} ({account_info["ilink_user_id"]})'
        overrides.append({
            "id": instance_id,
            "display_name": str(display_name),
        })
    return overrides


def _read_account_info(state_dir: Path) -> dict[str, str]:
    """Read the saved nickname and ilink_user_id from account.json, if present."""
    try:
        payload = json.loads((state_dir / "account.json").read_text(encoding="utf-8"))
        return {
            "nickname": str(payload.get("nickname", "") or "").strip(),
            "ilink_user_id": str(payload.get("ilink_user_id", "") or "").strip(),
        }
    except (OSError, ValueError, TypeError):
        return {"nickname": "", "ilink_user_id": ""}


def remove_weixin_instance(
    section: Any,
    *,
    instance_id: str = DEFAULT_INSTANCE_ID,
) -> dict[str, Any]:
    """Return canonical WeChat section with one instance removed.

    The default instance is never removed — only its login state is cleared.
    """
    instance_id = validate_instance_id(instance_id)
    canonical = canonical_weixin_section(section, weixin_default_config())
    instances = canonical.setdefault("instances", [])
    if instance_id == DEFAULT_INSTANCE_ID:
        # Reset the default instance to its base config instead of removing it.
        for instance in instances:
            if instance.get("id") == DEFAULT_INSTANCE_ID or instance.get("instanceId") == DEFAULT_INSTANCE_ID:
                base = _normalize_weixin_instance(
                    {"id": DEFAULT_INSTANCE_ID},
                    weixin_default_config(),
                    fallback_id=DEFAULT_INSTANCE_ID,
                )
                instance.clear()
                instance.update(base)
                break
        return canonical
    canonical["instances"] = [
        instance
        for instance in instances
        if instance.get("id") != instance_id and instance.get("instanceId") != instance_id
    ]
    return canonical


WEIXIN_MANAGEMENT = ChannelManagementSpec(
    multi_instance=True,
    default_config=weixin_default_config,
    instance_specs=managed_weixin_instance_specs,
    update_instance_config=update_managed_weixin_instance,
    runtime_name=runtime_channel_name,
    feature_instances=managed_weixin_feature_instances,
    local_state_present=local_state_present,
)


__all__ = [
    "DEFAULT_INSTANCE_ID",
    "WEIXIN_MANAGEMENT",
    "canonical_weixin_section",
    "remove_weixin_instance",
    "runtime_channel_name",
    "upsert_weixin_instance",
    "validate_instance_id",
    "weixin_default_config",
    "weixin_instance_specs",
]
