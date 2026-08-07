"""Tests for WeChat multi-instance configuration management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.channels.weixin.instances import (
    canonical_weixin_section,
    runtime_channel_name,
    upsert_weixin_instance,
    validate_instance_id,
    weixin_default_config,
    weixin_instance_specs,
)
from nanobot.channels.weixin.state import local_state_present
from nanobot.config.loader import save_config
from nanobot.config.schema import Config


async def _noop_async(self: object) -> None:
    """Async no-op for monkeypatching async methods."""
    pass


def test_validate_instance_id_accepts_valid_ids() -> None:
    assert validate_instance_id("default") == "default"
    assert validate_instance_id("assistant-1") == "assistant-1"
    assert validate_instance_id("work_bot") == "work_bot"
    assert validate_instance_id("  spaced  ") == "spaced"


def test_validate_instance_id_rejects_invalid_ids() -> None:
    with pytest.raises(ValueError):
        validate_instance_id("")
    with pytest.raises(ValueError):
        validate_instance_id("has space")
    with pytest.raises(ValueError):
        validate_instance_id("has/slash")
    with pytest.raises(ValueError):
        validate_instance_id("has.dot")


def test_runtime_channel_name() -> None:
    assert runtime_channel_name("weixin", "default") == "weixin"
    assert runtime_channel_name("weixin", "work") == "weixin.work"
    assert runtime_channel_name("weixin", "assistant-1") == "weixin.assistant-1"


def test_weixin_default_config_has_instance_fields() -> None:
    config = weixin_default_config()
    assert config["instanceId"] == "default"
    assert config["name"] == "nanobot"
    assert config["enabled"] is False


def test_instance_specs_from_legacy_flat_config() -> None:
    """Legacy flat config (no instances list) should produce one default instance."""
    section = {"enabled": True, "token": "abc", "allowFrom": ["*"]}
    specs = weixin_instance_specs(section, weixin_default_config(), enabled_only=False)
    assert len(specs) == 1
    assert specs[0].instance_id == "default"
    assert specs[0].config["enabled"] is True
    assert specs[0].config["token"] == "abc"


def test_instance_specs_from_multi_instance_config() -> None:
    """Multi-instance config should expand to multiple instance specs."""
    section = {
        "instances": [
            {"id": "default", "enabled": True, "token": "tok-1"},
            {"id": "work", "enabled": True, "token": "tok-2"},
        ]
    }
    specs = weixin_instance_specs(section, weixin_default_config(), enabled_only=False)
    assert len(specs) == 2
    assert specs[0].instance_id == "default"
    assert specs[0].config["token"] == "tok-1"
    assert specs[1].instance_id == "work"
    assert specs[1].config["token"] == "tok-2"


def test_instance_specs_enabled_only_filters_disabled() -> None:
    section = {
        "instances": [
            {"id": "default", "enabled": True},
            {"id": "disabled-one", "enabled": False},
            {"id": "work", "enabled": True},
        ]
    }
    specs = weixin_instance_specs(section, weixin_default_config(), enabled_only=True)
    assert len(specs) == 2
    assert {s.instance_id for s in specs} == {"default", "work"}


def test_instance_specs_deduplicates_duplicate_ids() -> None:
    section = {
        "instances": [
            {"id": "default", "enabled": True},
            {"id": "default", "enabled": True},
        ]
    }
    specs = weixin_instance_specs(section, weixin_default_config(), enabled_only=False)
    assert len(specs) == 1


def test_instance_specs_inherits_section_level_values() -> None:
    section = {
        "base_url": "https://custom.example",
        "instances": [
            {"id": "default", "enabled": True},
        ]
    }
    specs = weixin_instance_specs(section, weixin_default_config(), enabled_only=False)
    assert specs[0].config["base_url"] == "https://custom.example"


def test_canonical_weixin_section_from_legacy() -> None:
    section = {"enabled": True, "token": "abc"}
    canonical = canonical_weixin_section(section, weixin_default_config())
    assert "instances" in canonical
    assert len(canonical["instances"]) == 1
    assert canonical["instances"][0]["instanceId"] == "default"


def test_canonical_weixin_section_from_multi_instance() -> None:
    section = {
        "instances": [
            {"id": "default", "enabled": True},
            {"id": "work", "enabled": True, "token": "tok-work"},
        ]
    }
    canonical = canonical_weixin_section(section, weixin_default_config())
    assert len(canonical["instances"]) == 2
    assert canonical["instances"][0]["id"] == "default"
    assert canonical["instances"][1]["id"] == "work"


def test_upsert_creates_new_instance() -> None:
    section = {"instances": [{"id": "default", "enabled": True}]}
    updated = upsert_weixin_instance(
        section,
        weixin_default_config(),
        "work",
        {"enabled": True, "token": "tok-work"},
    )
    assert len(updated["instances"]) == 2
    work = next(i for i in updated["instances"] if i["id"] == "work")
    assert work["token"] == "tok-work"
    assert work["enabled"] is True


def test_upsert_updates_existing_instance() -> None:
    section = {
        "instances": [
            {"id": "default", "enabled": True, "token": "old"},
            {"id": "work", "enabled": True, "token": "old-work"},
        ]
    }
    updated = upsert_weixin_instance(
        section,
        weixin_default_config(),
        "work",
        {"token": "new-work"},
    )
    assert len(updated["instances"]) == 2
    work = next(i for i in updated["instances"] if i["id"] == "work")
    assert work["token"] == "new-work"
    # default should be unchanged
    default = next(i for i in updated["instances"] if i["id"] == "default")
    assert default["token"] == "old"


def test_upsert_preserves_other_instances() -> None:
    section = {
        "instances": [
            {"id": "default", "enabled": True, "token": "tok-1"},
            {"id": "work", "enabled": True, "token": "tok-2"},
        ]
    }
    updated = upsert_weixin_instance(
        section,
        weixin_default_config(),
        "personal",
        {"enabled": True},
    )
    assert len(updated["instances"]) == 3
    ids = {i["id"] for i in updated["instances"]}
    assert ids == {"default", "work", "personal"}


def test_local_state_present_legacy_flat_config(tmp_path: Path) -> None:
    """Legacy flat config checks for account.json in stateDir."""
    state_dir = tmp_path / "weixin-state"
    state_dir.mkdir()
    section: dict[str, Any] = {"stateDir": str(state_dir)}

    assert local_state_present(section) is False

    (state_dir / "account.json").write_text(
        json.dumps({"token": "tok-1"}), encoding="utf-8"
    )
    assert local_state_present(section) is True


def test_local_state_present_multi_instance_config(tmp_path: Path) -> None:
    """Multi-instance config checks each instance's state directory."""
    default_dir = tmp_path / "weixin" / "default"
    work_dir = tmp_path / "weixin" / "work"
    default_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)

    section: dict[str, Any] = {
        "instances": [
            {"id": "default", "enabled": True, "stateDir": str(default_dir)},
            {"id": "work", "enabled": True, "stateDir": str(work_dir)},
        ]
    }

    assert local_state_present(section) is False

    # Save token for work instance only
    (work_dir / "account.json").write_text(
        json.dumps({"token": "tok-work"}), encoding="utf-8"
    )
    assert local_state_present(section) is True


def test_local_state_present_multi_instance_derived_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stateDir is not set, derive it from instance_id."""
    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    # The derived dir for "default" should be tmp_path / "weixin"
    default_dir = tmp_path / "weixin"
    default_dir.mkdir()

    section: dict[str, Any] = {
        "instances": [
            {"id": "default", "enabled": True},
        ]
    }

    assert local_state_present(section) is False

    (default_dir / "account.json").write_text(
        json.dumps({"token": "tok-default"}), encoding="utf-8"
    )
    assert local_state_present(section) is True


def test_weixin_config_has_instance_id_and_name() -> None:
    from nanobot.channels.weixin.runtime import WeixinConfig

    config = WeixinConfig()
    assert config.instance_id == "default"
    assert config.name == "nanobot"

    custom = WeixinConfig(instance_id="work", name="work bot")
    assert custom.instance_id == "work"
    assert custom.name == "work bot"


def test_state_dir_derived_from_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-default instances should use a subdirectory under weixin/."""
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.weixin.runtime import WeixinChannel, WeixinConfig

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    # Default instance: state dir = <data>/weixin/
    channel_default = WeixinChannel(
        WeixinConfig(enabled=True, allow_from=["*"]),
        MessageBus(),
    )
    assert channel_default._get_state_dir() == tmp_path / "weixin"

    # Non-default instance: state dir = <data>/weixin/<instance_id>/
    channel_work = WeixinChannel(
        WeixinConfig(enabled=True, allow_from=["*"], instance_id="work"),
        MessageBus(),
    )
    assert channel_work._get_state_dir() == tmp_path / "weixin" / "work"


@pytest.mark.asyncio
async def test_connect_store_start_with_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WeixinConnectStore.start should accept instance_id and build channel with it."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(
        Config.model_validate({
            "channels": {
                "weixin": {
                    "instances": [
                        {"id": "default", "enabled": True},
                        {"id": "work", "enabled": True},
                    ]
                }
            }
        }),
        config_path,
    )
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    async def fake_fetch_qr_code(self: WeixinChannel) -> tuple[str, str]:
        return "qr-1", "https://qr.example/1"

    monkeypatch.setattr(WeixinChannel, "_fetch_qr_code", fake_fetch_qr_code)

    store = WeixinConnectStore()
    started = await store.start(instance_id="work")

    assert started["status"] == "pending"
    assert started["instance_id"] == "work"
    assert started["qr_url"] == "https://qr.example/1"

    # The channel built for this session should have instance_id="work"
    session = store._sessions[started["session_id"]]
    assert session.instance_id == "work"
    assert session.channel.config.instance_id == "work"


@pytest.mark.asyncio
async def test_connect_store_poll_returns_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll response should include instance_id."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    async def fake_fetch_qr_code(self: WeixinChannel) -> tuple[str, str]:
        return "qr-1", "https://qr.example/1"

    async def fake_api_get_with_base(
        self: WeixinChannel,
        *,
        base_url: str,
        endpoint: str,
        params: dict[str, Any],
        auth: bool,
    ) -> dict[str, str]:
        return {
            "status": "confirmed",
            "bot_token": "wx-token",
            "baseurl": "https://weixin.example",
            "ilink_user_id": "wx-user",
        }

    monkeypatch.setattr(WeixinChannel, "_fetch_qr_code", fake_fetch_qr_code)
    monkeypatch.setattr(WeixinChannel, "_api_get_with_base", fake_api_get_with_base)

    store = WeixinConnectStore()
    started = await store.start(instance_id="work")
    completed = await store.poll(started["session_id"])

    assert completed["status"] == "succeeded"
    assert completed["instance_id"] == "work"
    assert completed["account"] == "wx-user"


@pytest.mark.asyncio
async def test_connect_store_create_mode_generates_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mode='create' should generate a new unique instance id."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    async def fake_fetch_qr_code(self: WeixinChannel) -> tuple[str, str]:
        return "qr-1", "https://qr.example/1"

    monkeypatch.setattr(WeixinChannel, "_fetch_qr_code", fake_fetch_qr_code)

    store = WeixinConnectStore()
    started = await store.start(mode="create")

    assert started["status"] == "pending"
    assert started["instance_id"].startswith("assistant-")
    assert started["instance_id"] != "default"


# ---------------------------------------------------------------------------
# Account identity and disconnect tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_commit_saves_ilink_user_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect_commit_account should persist ilink_user_id to account.json."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    async def fake_fetch_qr_code(self: WeixinChannel) -> tuple[str, str]:
        return ("qr-1", "https://weixin.example/qr.png")

    async def fake_api_get_with_base(
        self: WeixinChannel,
        *,
        base_url: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        return {
            "status": "confirmed",
            "bot_token": "tok_save_user",
            "baseurl": "https://weixin.example",
            "ilink_user_id": "wxid_abc123",
        }

    monkeypatch.setattr(WeixinChannel, "_fetch_qr_code", fake_fetch_qr_code)
    monkeypatch.setattr(WeixinChannel, "_api_get_with_base", fake_api_get_with_base)

    store = WeixinConnectStore()
    started = await store.start(instance_id="acct1")
    completed = await store.poll(started["session_id"])

    assert completed["status"] == "succeeded"
    assert completed["account"] == "wxid_abc123"

    # Verify the ilink_user_id was persisted to account.json
    from nanobot.config.paths import get_config_path

    state_file = get_config_path().parent / "weixin" / "acct1" / "account.json"
    assert state_file.exists()
    saved = json.loads(state_file.read_text())
    assert saved["ilink_user_id"] == "wxid_abc123"
    assert saved["token"] == "tok_save_user"


@pytest.mark.asyncio
async def test_connect_load_state_restores_ilink_user_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_load_state should restore ilink_user_id from account.json."""
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    state_dir = config_path.parent / "weixin" / "acct2"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_restore",
        "ilink_user_id": "wxid_restore_me",
        "base_url": "https://weixin.example",
    }))

    channel = WeixinChannel({"instanceId": "acct2"}, MessageBus())
    assert channel.connect_load_state()
    assert channel.connect_account_id == "wxid_restore_me"


@pytest.mark.asyncio
async def test_already_connected_returns_account_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start() should return the account field when already connected."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    state_dir = config_path.parent / "weixin" / "acct3"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_existing",
        "ilink_user_id": "wxid_existing_acct",
        "base_url": "https://weixin.example",
    }))

    monkeypatch.setattr(WeixinChannel, "connect_open_client", lambda self: None)

    store = WeixinConnectStore()
    result = await store.start(instance_id="acct3")

    assert result["status"] == "succeeded"
    assert result["account"] == "wxid_existing_acct"


@pytest.mark.asyncio
async def test_disconnect_clears_saved_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect should delete account.json and return cleared=True."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    state_dir = config_path.parent / "weixin" / "acct4"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_disconnect",
        "ilink_user_id": "wxid_disconnect_me",
        "base_url": "https://weixin.example",
    }))

    monkeypatch.setattr(WeixinChannel, "connect_close_client", _noop_async)

    store = WeixinConnectStore()
    result = await store.disconnect(instance_id="acct4")

    assert result["status"] == "succeeded"
    assert result["cleared"] is True
    assert not (state_dir / "account.json").exists()


@pytest.mark.asyncio
async def test_disconnect_without_existing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect should return cleared=False when no state exists."""
    from nanobot.channels.weixin.connect import WeixinConnectStore
    from nanobot.channels.weixin.runtime import WeixinChannel

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)
    monkeypatch.setattr(WeixinChannel, "connect_close_client", _noop_async)

    store = WeixinConnectStore()
    result = await store.disconnect(instance_id="acct5")

    assert result["status"] == "succeeded"
    assert result["cleared"] is False


@pytest.mark.asyncio
async def test_disconnect_rejects_invalid_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect should reject invalid instance ids."""
    from nanobot.channels.connect import ChannelConnectError
    from nanobot.channels.weixin.connect import WeixinConnectStore

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    store = WeixinConnectStore()
    with pytest.raises(ChannelConnectError):
        await store.disconnect(instance_id="has space")


def test_feature_instances_includes_account_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """feature_instances should include the WeChat user ID in display_name."""
    from nanobot.channels.weixin.instances import managed_weixin_feature_instances

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    # Create a saved state with ilink_user_id
    state_dir = config_path.parent / "weixin" / "acct6"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_feature",
        "ilink_user_id": "wxid_feature_test",
    }))

    section = {
        "instances": [
            {"id": "acct6", "name": "MyBot", "enabled": True},
        ]
    }

    overrides = managed_weixin_feature_instances(section)
    assert overrides is not None
    assert len(overrides) == 1
    assert overrides[0]["id"] == "acct6"
    assert "wxid_feature_test" in overrides[0]["display_name"]


def test_feature_instances_without_saved_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """feature_instances should still work when no account.json exists."""
    from nanobot.channels.weixin.instances import managed_weixin_feature_instances

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    section = {
        "instances": [
            {"id": "default", "name": "nanobot", "enabled": True},
        ]
    }

    overrides = managed_weixin_feature_instances(section)
    assert overrides is not None
    assert len(overrides) == 1
    assert overrides[0]["id"] == "default"
    assert "wxid" not in overrides[0]["display_name"]


def test_feature_instances_shows_nickname_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """feature_instances should show the WeChat nickname as display_name when available."""
    from nanobot.channels.weixin.instances import managed_weixin_feature_instances

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    state_dir = config_path.parent / "weixin" / "acct_nick"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_nick",
        "ilink_user_id": "wxid_internal_123",
        "nickname": "张三",
    }))

    section = {
        "instances": [
            {"id": "acct_nick", "name": "nanobot", "enabled": True},
        ]
    }

    overrides = managed_weixin_feature_instances(section)
    assert overrides is not None
    assert len(overrides) == 1
    assert overrides[0]["display_name"] == "张三"


def test_feature_instances_falls_back_to_ilink_user_id_without_nickname(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """feature_instances should fall back to ilink_user_id when nickname is absent."""
    from nanobot.channels.weixin.instances import managed_weixin_feature_instances

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    state_dir = config_path.parent / "weixin" / "acct_fallback"
    state_dir.mkdir(parents=True)
    (state_dir / "account.json").write_text(json.dumps({
        "token": "tok_fallback",
        "ilink_user_id": "wxid_fallback_only",
    }))

    section = {
        "instances": [
            {"id": "acct_fallback", "name": "MyBot", "enabled": True},
        ]
    }

    overrides = managed_weixin_feature_instances(section)
    assert overrides is not None
    assert overrides[0]["display_name"] == "MyBot (wxid_fallback_only)"


def test_remove_weixin_instance_removes_non_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remove_weixin_instance should remove a non-default instance from the section."""
    from nanobot.channels.weixin.instances import remove_weixin_instance

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    section = {
        "instances": [
            {"id": "default", "name": "nanobot", "enabled": True},
            {"id": "acct_remove", "name": "ToRemove", "enabled": True},
            {"id": "acct_keep", "name": "ToKeep", "enabled": True},
        ]
    }

    result = remove_weixin_instance(section, instance_id="acct_remove")
    ids = [inst["id"] for inst in result["instances"]]
    assert "acct_remove" not in ids
    assert "default" in ids
    assert "acct_keep" in ids


def test_remove_weixin_instance_resets_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remove_weixin_instance should reset (not remove) the default instance."""
    from nanobot.channels.weixin.instances import remove_weixin_instance

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({}), config_path)
    monkeypatch.setattr("nanobot.config.loader._current_config_path", config_path)

    section = {
        "instances": [
            {"id": "default", "name": "custom_name", "enabled": True, "token": "abc"},
            {"id": "acct_other", "name": "Other", "enabled": True},
        ]
    }

    result = remove_weixin_instance(section, instance_id="default")
    ids = [inst["id"] for inst in result["instances"]]
    assert "default" in ids
    assert "acct_other" in ids
    default_inst = next(inst for inst in result["instances"] if inst["id"] == "default")
    # The default instance should be reset to base config (no custom token)
    assert default_inst.get("token", "") == ""
