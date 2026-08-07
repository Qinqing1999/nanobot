"""WeChat-owned interactive connection flow."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from nanobot.channels.connect import ChannelConnectError, QueryParams, query_first
from nanobot.channels.weixin.instances import (
    DEFAULT_INSTANCE_ID,
    remove_weixin_instance,
    validate_instance_id,
)
from nanobot.config.loader import load_config, save_config

if TYPE_CHECKING:
    from nanobot.channels.weixin.runtime import WeixinChannel


@dataclass(slots=True)
class WeixinConnectSession:
    id: str
    instance_id: str
    instance_name: str
    qrcode_id: str
    qr_url: str
    channel: WeixinChannel
    current_poll_base_url: str
    refresh_count: int
    created_wall: float
    deadline: float
    last_error: str | None = None


class WeixinConnectStore:
    """In-memory WeChat QR login sessions for the WebUI."""

    def __init__(self) -> None:
        self._sessions: dict[str, WeixinConnectSession] = {}

    async def handle(self, action: str, query: QueryParams) -> dict[str, Any]:
        """Handle one generic settings connection action."""
        if action == "start":
            instance_id = (query_first(query, "instance_id") or "default").strip()
            mode = (query_first(query, "mode") or "replace").strip()
            force = (query_first(query, "force") or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
            return await self.start(force=force, instance_id=instance_id, mode=mode)

        if action == "disconnect":
            instance_id = (query_first(query, "instance_id") or "default").strip()
            return await self.disconnect(instance_id=instance_id)

        if action == "delete":
            instance_id = (query_first(query, "instance_id") or "default").strip()
            return await self.delete_instance(instance_id=instance_id)

        session_id = (query_first(query, "session_id") or "").strip()
        if not session_id:
            raise ChannelConnectError("missing WeChat connect session")
        if action == "poll":
            return await self.poll(session_id)
        if action == "cancel":
            return await self.cancel(session_id)
        raise ChannelConnectError(f"unsupported WeChat connect action: {action}", status=404)

    async def start(
        self,
        *,
        force: bool = False,
        instance_id: str = DEFAULT_INSTANCE_ID,
        mode: str = "replace",
    ) -> dict[str, Any]:
        instance_id = _resolve_instance_id(instance_id, mode)
        await self._cleanup()

        channel = self._build_channel(instance_id=instance_id)
        if force:
            # Preserve the working account until a replacement scan succeeds.
            channel.connect_reset_pending_credentials()
        elif channel.connect_load_state():
            return {
                "session_id": "",
                "instance_id": instance_id,
                "status": "succeeded",
                "message": "WeChat is already connected.",
                "account": channel.connect_account_id,
                "nickname": channel.connect_account_nickname,
                "interval_ms": 2000,
            }

        channel.connect_open_client()
        try:
            qrcode_id, qr_url = await channel.connect_fetch_qr_code()
        except Exception as exc:
            await self._close_channel(channel)
            raise ChannelConnectError(
                f"Unable to start WeChat QR login: {exc}",
                status=502,
            ) from exc

        session_id = secrets.token_urlsafe(18)
        now_wall = time.time()
        self._sessions[session_id] = WeixinConnectSession(
            id=session_id,
            instance_id=instance_id,
            instance_name=_default_instance_name(instance_id),
            qrcode_id=qrcode_id,
            qr_url=qr_url,
            channel=channel,
            current_poll_base_url=channel.connect_base_url,
            refresh_count=0,
            created_wall=now_wall,
            deadline=time.monotonic() + 600,
        )
        return self._start_payload(self._sessions[session_id])

    async def poll(self, session_id: str) -> dict[str, Any]:
        await self._cleanup()
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "status": "expired",
                "message": "This WeChat login has expired. Start again.",
            }

        try:
            status_data = await session.channel.connect_poll_qr_code(
                base_url=session.current_poll_base_url,
                qrcode_id=session.qrcode_id,
            )
        except Exception as exc:
            if session.channel.connect_poll_error_is_retryable(exc):
                session.last_error = str(exc)
                return self._pending_payload(session)
            self._sessions.pop(session_id, None)
            await self._close_channel(session.channel)
            return {
                "session_id": session_id,
                "instance_id": session.instance_id,
                "status": "failed",
                "message": f"WeChat QR login failed: {exc}",
            }

        status_payload = status_data
        status = status_payload.get("status", "")
        if status == "confirmed":
            if self._sessions.get(session_id) is not session:
                return {
                    "session_id": session_id,
                    "instance_id": session.instance_id,
                    "status": "cancelled",
                    "message": "WeChat login cancelled.",
                }
            token = str(status_payload.get("bot_token", "") or "")
            if not token:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "instance_id": session.instance_id,
                    "status": "failed",
                    "message": "WeChat confirmed the scan but returned no token.",
                }
            base_url = str(status_payload.get("baseurl", "") or "")
            ilink_user_id = str(status_payload.get("ilink_user_id", "") or "")
            nickname = str(status_payload.get("nickname", "") or "")
            session.channel.connect_commit_account(
                token=token,
                base_url=base_url,
                ilink_user_id=ilink_user_id,
                nickname=nickname,
            )
            self._sessions.pop(session_id, None)
            await self._close_channel(session.channel)
            return {
                "session_id": session_id,
                "instance_id": session.instance_id,
                "status": "succeeded",
                "message": "WeChat is connected.",
                "account": ilink_user_id,
                "nickname": nickname,
            }

        if status == "scaned_but_redirect":
            redirect_host = str(status_payload.get("redirect_host", "") or "").strip()
            if redirect_host:
                session.current_poll_base_url = (
                    redirect_host
                    if redirect_host.startswith(("http://", "https://"))
                    else f"https://{redirect_host}"
                )
            return self._pending_payload(session)

        if status == "expired":
            from nanobot.channels.weixin.runtime import MAX_QR_REFRESH_COUNT

            session.refresh_count += 1
            if session.refresh_count > MAX_QR_REFRESH_COUNT:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "instance_id": session.instance_id,
                    "status": "expired",
                    "message": "This WeChat QR code expired. Start again.",
                }
            try:
                session.qrcode_id, session.qr_url = (
                    await session.channel.connect_fetch_qr_code()
                )
            except Exception as exc:
                self._sessions.pop(session_id, None)
                await self._close_channel(session.channel)
                return {
                    "session_id": session_id,
                    "instance_id": session.instance_id,
                    "status": "failed",
                    "message": f"Could not refresh WeChat QR code: {exc}",
                }
            session.current_poll_base_url = session.channel.connect_base_url
            return self._pending_payload(session)

        return self._pending_payload(session)

    async def cancel(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await self._close_channel(session.channel)
        return {
            "session_id": session_id,
            "instance_id": session.instance_id if session else DEFAULT_INSTANCE_ID,
            "status": "cancelled",
            "message": "WeChat login cancelled.",
        }

    async def disconnect(self, *, instance_id: str = DEFAULT_INSTANCE_ID) -> dict[str, Any]:
        """Delete saved login state for a WeChat instance."""
        try:
            instance_id = validate_instance_id(instance_id or DEFAULT_INSTANCE_ID)
        except ValueError as exc:
            raise ChannelConnectError(str(exc), status=400) from exc

        channel = self._build_channel(instance_id=instance_id)
        cleared = channel.connect_clear_account()
        await self._close_channel(channel)
        return {
            "session_id": "",
            "instance_id": instance_id,
            "status": "succeeded",
            "message": "WeChat account disconnected." if cleared else "No WeChat account was connected.",
            "cleared": cleared,
        }

    async def delete_instance(self, *, instance_id: str = DEFAULT_INSTANCE_ID) -> dict[str, Any]:
        """Delete saved login state and remove the instance from config."""
        try:
            instance_id = validate_instance_id(instance_id or DEFAULT_INSTANCE_ID)
        except ValueError as exc:
            raise ChannelConnectError(str(exc), status=400) from exc

        # Clear the saved account state first.
        channel = self._build_channel(instance_id=instance_id)
        cleared = channel.connect_clear_account()
        await self._close_channel(channel)

        # Remove the instance from the config (default instance is reset, not removed).
        config = load_config()
        section = getattr(config.channels, "weixin", None)
        if section is not None and hasattr(section, "model_dump"):
            section_data = section.model_dump(mode="json", by_alias=True)
        elif isinstance(section, dict):
            section_data = dict(cast(dict[str, Any], section))
        else:
            section_data = {}

        updated_section = remove_weixin_instance(section_data, instance_id=instance_id)
        setattr(config.channels, "weixin", updated_section)
        save_config(config)

        return {
            "session_id": "",
            "instance_id": instance_id,
            "status": "succeeded",
            "message": "WeChat instance deleted.",
            "cleared": cleared,
        }

    async def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now >= session.deadline
        ]
        for session_id in expired:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                await self._close_channel(session.channel)

    @staticmethod
    def _build_channel(*, instance_id: str = DEFAULT_INSTANCE_ID) -> WeixinChannel:
        from nanobot.bus.queue import MessageBus
        from nanobot.channels.weixin.runtime import WeixinChannel

        section = getattr(load_config().channels, "weixin", None)
        if section is not None and hasattr(section, "model_dump"):
            config = section.model_dump(mode="json", by_alias=True)
        elif isinstance(section, dict):
            config = dict(cast(dict[str, Any], section))
        else:
            config = {}

        # Resolve instance-specific config from multi-instance section
        config = _resolve_instance_config(config, instance_id)
        return WeixinChannel(config, MessageBus())

    @staticmethod
    async def _close_channel(channel: WeixinChannel) -> None:
        await channel.connect_close_client()

    @staticmethod
    def _start_payload(session: WeixinConnectSession) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "instance_id": session.instance_id,
            "status": "pending",
            "qr_url": session.qr_url,
            "interval_ms": 2000,
            "expires_at_ms": int((session.created_wall + 600) * 1000),
            "message": "Scan with WeChat to connect.",
        }

    @staticmethod
    def _pending_payload(session: WeixinConnectSession) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "instance_id": session.instance_id,
            "status": "pending",
            "qr_url": session.qr_url,
            "interval_ms": 2000,
            "expires_at_ms": int((session.created_wall + 600) * 1000),
            "message": "Waiting for WeChat scan.",
        }


def _resolve_instance_id(instance_id: str, mode: str) -> str:
    if mode == "create":
        return f"assistant-{secrets.token_hex(3)}"
    try:
        return validate_instance_id(instance_id or DEFAULT_INSTANCE_ID)
    except ValueError as exc:
        raise ChannelConnectError(str(exc), status=400) from exc


def _default_instance_name(instance_id: str) -> str:
    return "nanobot" if instance_id == DEFAULT_INSTANCE_ID else f"nanobot {instance_id}"


def _resolve_instance_config(
    config: dict[str, Any],
    instance_id: str,
) -> dict[str, Any]:
    """Extract and merge config for a specific instance from a multi-instance section."""
    instances = config.get("instances")
    if isinstance(instances, list):
        # Find the matching instance
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            iid = str(
                instance.get("id")
                or instance.get("instanceId")
                or instance.get("instance_id")
                or "default"
            )
            if iid == instance_id:
                # Merge inherited (section-level) values with instance-specific values
                inherited = {
                    key: value
                    for key, value in config.items()
                    if key != "instances"
                }
                merged = dict(inherited)
                merged.update(instance)
                merged["instanceId"] = instance_id
                return merged
        # Instance not found — return defaults with instance_id set
        return {"instanceId": instance_id, "enabled": True}
    # Legacy flat config — ensure instance_id is set
    config = dict(config)
    config.setdefault("instanceId", instance_id)
    return config


__all__ = ["WeixinConnectStore"]
