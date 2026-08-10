"""Mask application tool — crops a subject from an image using a mask."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.image_utils import ImageInputError, resolve_image_input, resolve_mask_path
from nanobot.utils.artifacts import (
    generated_image_tool_result,
    store_generated_image_artifact,
)

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus
    from nanobot.session.manager import SessionManager


class ApplyMaskError(RuntimeError):
    """Raised when mask application fails."""


@tool_parameters({
    "type": "object",
    "properties": {
        "image": {
            "type": "string",
            "description": (
                "原图路径或制品 ID。"
                "支持工作区内文件路径、media 目录路径、"
                "或四位数字制品 ID。"
            ),
        },
        "mask": {
            "type": "string",
            "description": (
                "蒙版文件路径（由 segment_subject 工具返回的 mask_path）。"
                "黑白 PNG：白色=保留区域，黑色=替换为背景的区域。"
                "如果蒙版尺寸与原图不同，将自动缩放到原图尺寸。"
            ),
        },
        "background": {
            "type": "string",
            "description": "背景填充方式：white（默认，纯白）或 transparent（透明）。",
            "enum": ["white", "transparent"],
        },
    },
    "required": ["image", "mask"],
})
class ApplyMaskTool(Tool):
    """Apply a mask to an image, producing a clean subject image with background removed."""

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        """Always enabled — pure image processing, no external service."""
        return True

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(
            workspace=ctx.workspace,
            sessions=ctx.sessions,
            bus=ctx.bus,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        sessions: SessionManager | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self._sessions = sessions
        self._bus = bus

    @property
    def name(self) -> str:
        return "apply_mask"

    @property
    def description(self) -> str:
        return (
            "用蒙版裁剪原图，生成干净的主体图。\n"
            "蒙版白色区域保留原图像素，黑色区域替换为指定背景色。\n"
            "输入原图路径或制品 ID，以及 segment_subject 返回的蒙版路径。\n"
            "输出注册为图片制品，可获得制品 ID 用于后续引用。\n"
            "典型工作流：segment_subject（生成分割蒙版）→ apply_mask（裁剪主体）。\n"
            "通常在 segment_subject 之后调用，用于提取图片主体并去除背景干扰。"
        )

    async def execute(
        self,
        image: str,
        mask: str,
        background: str = "white",
        **kwargs: Any,
    ) -> str:
        # 1. Resolve image path (artifact ID or file path)
        try:
            image_path = resolve_image_input(image, self.workspace, self._sessions)
        except ImageInputError as exc:
            return ToolResult.error(str(exc))

        # 2. Resolve mask path (file path only, never artifact ID)
        try:
            mask_path = resolve_mask_path(mask, self.workspace)
        except ImageInputError as exc:
            return ToolResult.error(str(exc))

        # 3. Read image and mask bytes
        try:
            image_bytes = image_path.read_bytes()
            mask_bytes = mask_path.read_bytes()
        except OSError as exc:
            return ToolResult.error(f"读取文件失败: {exc}")

        # 4. Validate image formats
        from nanobot.utils.helpers import detect_image_mime
        if detect_image_mime(image_bytes) is None:
            return ToolResult.error(f"不支持的图片格式: {image}")
        if detect_image_mime(mask_bytes) is None:
            return ToolResult.error(f"蒙版文件不是有效图片: {mask}")

        # 5. Apply mask (Pillow image compositing)
        try:
            result_data_url = self._apply_mask(image_bytes, mask_bytes, background=background)
        except ApplyMaskError as exc:
            return ToolResult.error(str(exc))
        except Exception as exc:
            logger.exception("Mask application failed")
            return ToolResult.error(f"图像处理失败: {exc}")

        # 6. Save as artifact
        artifact = store_generated_image_artifact(
            result_data_url,
            prompt=f"主体裁剪（蒙版: {mask_path.name}）",
            model="apply_mask",
            source_images=[str(image_path)],
            save_dir="generated",
            provider="segmentation",
        )

        # 7. Register in session Artifact Registry
        self._register_artifact(artifact, prompt=f"主体裁剪（蒙版: {mask_path.name}）")

        # 8. Push artifact notification to user
        if self._bus:
            await self._push_artifact_notification(artifact)

        logger.info("Mask applied: artifact {} saved", artifact.get("id", "unknown"))

        return generated_image_tool_result([artifact])

    def _apply_mask(
        self,
        image_bytes: bytes,
        mask_bytes: bytes,
        *,
        background: str = "white",
    ) -> str:
        """Composite image with mask, producing a clean subject image."""
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        mask_img = Image.open(io.BytesIO(mask_bytes)).convert("L")

        # Auto-resize mask to match image dimensions if they differ
        if mask_img.size != img.size:
            logger.info(
                "Auto-resizing mask from {} to {} to match image",
                mask_img.size,
                img.size,
            )
            mask_img = mask_img.resize(img.size, Image.BILINEAR)

        if background == "transparent":
            # Transparent background: attach mask as alpha channel
            result = img.convert("RGBA")
            result.putalpha(mask_img)
        else:
            # White background: composite image over white using mask
            white_bg = Image.new("RGB", img.size, (255, 255, 255))
            result = Image.composite(img, white_bg, mask_img)

        # Convert to base64 data URL
        buf = io.BytesIO()
        result.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _register_artifact(self, artifact: dict[str, Any], *, prompt: str) -> None:
        """Register a generated artifact in the session's artifact registry."""
        if self._sessions is None:
            return
        from nanobot.agent.tools.context import current_request_context

        ctx = current_request_context()
        if ctx is None or not ctx.session_key:
            return

        try:
            session = self._sessions.get_or_create(ctx.session_key)
            entry = session.artifact_registry.register(
                type="image",
                path=artifact.get("path", ""),
                filename=Path(artifact.get("path", "")).name,
                mime=artifact.get("mime", "image/png"),
                source="generated",
                prompt=prompt,
                model="apply_mask",
            )
            artifact["artifact_id"] = entry.id
            session.sync_artifact_registry()
            self._sessions.save(session)
        except Exception:
            logger.debug("Failed to register image artifact in session registry")

    async def _push_artifact_notification(self, artifact: dict[str, Any]) -> None:
        """Push artifact notification to the user via MessageBus."""
        from nanobot.agent.tools.context import current_request_context
        from nanobot.bus.events import OutboundMessage
        from nanobot.utils.artifact_registry import format_artifact_notification

        ctx = current_request_context()
        if ctx is None:
            return

        notification_artifacts = [
            {
                "id": artifact.get("artifact_id", ""),
                "type": "image",
                "filename": Path(artifact.get("path", "")).name,
            }
        ]
        notification = format_artifact_notification(notification_artifacts)
        await self._bus.publish_outbound(OutboundMessage(
            channel=ctx.channel,
            chat_id=ctx.chat_id,
            content=notification,
        ))
