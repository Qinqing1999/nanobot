"""Subject segmentation tool — calls BiRefNet to extract a subject mask."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.image_utils import ImageInputError, resolve_image_input
from nanobot.config.paths import get_media_dir
from nanobot.utils.artifacts import decode_image_data_url
from nanobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.session.manager import SessionManager


_SEGMENT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 5.0


class SegmentSubjectError(RuntimeError):
    """Raised when subject segmentation fails."""


@tool_parameters({
    "type": "object",
    "properties": {
        "image": {
            "type": "string",
            "description": (
                "要提取主体的图片路径或制品 ID。"
                "支持工作区内文件路径、nanobot media 目录路径、"
                "或四位数字制品 ID（如 '1021'）。"
            ),
        },
    },
    "required": ["image"],
})
class SegmentSubjectTool(Tool):
    """Call BiRefNet segmentation service to extract a subject mask from an image."""

    config_key = ""

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        """Enable when tools.image_generation.segmentation_api_base is configured."""
        api_base = getattr(ctx.config.image_generation, "segmentation_api_base", "")
        return bool(api_base)

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        api_base = getattr(ctx.config.image_generation, "segmentation_api_base", "") or "http://localhost:8001"
        return cls(
            workspace=ctx.workspace,
            api_base=api_base,
            sessions=ctx.sessions,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        api_base: str,
        sessions: SessionManager | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self._api_base = api_base.rstrip("/")
        self._sessions = sessions

    @property
    def name(self) -> str:
        return "segment_subject"

    @property
    def description(self) -> str:
        return (
            "从图片中自动识别主体（人物、物品、动物等）并生成分割蒙版。"
            "输入图片路径或制品 ID，返回蒙版文件路径。"
            "蒙版是黑白 PNG：白色标记主体区域，黑色标记背景区域。"
            "蒙版为临时文件，需配合 apply_mask 工具使用以生成干净主体图。"
            "仅在需要提取图片主体时调用此工具。"
            "如果用户只想生成图片，使用 generate_image 工具即可，无需先提取主体。"
        )

    async def execute(self, image: str, **kwargs: Any) -> str:
        try:
            image_path = resolve_image_input(image, self.workspace, self._sessions)
        except ImageInputError as exc:
            return ToolResult.error(str(exc))

        # Read image and convert to base64 data URL
        try:
            raw_bytes = image_path.read_bytes()
        except OSError as exc:
            return ToolResult.error(f"无法读取图片文件: {exc}")

        from nanobot.utils.helpers import detect_image_mime
        mime = detect_image_mime(raw_bytes)
        if mime is None:
            return ToolResult.error(f"不支持的图片格式: {image}")

        data_url = f"data:{mime};base64,{base64.b64encode(raw_bytes).decode('ascii')}"

        # Call BiRefNet service
        try:
            mask_data_url = await self._call_segment_service(data_url)
        except SegmentSubjectError as exc:
            return ToolResult.error(str(exc))

        # Save mask to temporary file
        try:
            mask_path = self._save_mask(mask_data_url)
        except Exception as exc:
            logger.exception("Failed to save mask")
            return ToolResult.error(f"保存蒙版文件失败: {exc}")

        logger.info("Subject segmentation complete: mask saved to {}", mask_path)

        return json.dumps(
            {
                "mask_path": str(mask_path),
                "service": "segmentation",
                "next_step": (
                    "使用 apply_mask 工具，将 image 参数设为原图路径或制品 ID，"
                    "mask 参数设为此 mask_path，生成纯白背景的干净主体图。"
                ),
            },
            ensure_ascii=False,
        )

    async def _call_segment_service(self, image_data_url: str) -> str:
        """POST to BiRefNet /segment and return the mask data URL."""
        url = f"{self._api_base}/segment"
        headers = {"Content-Type": "application/json"}
        body: dict[str, Any] = {"image": image_data_url}

        try:
            async with httpx.AsyncClient(timeout=_SEGMENT_TIMEOUT_S) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.ConnectError as exc:
            raise SegmentSubjectError(
                f"无法连接分割服务 {self._api_base}。"
                "请确认 BiRefNet Docker 容器已启动（docker compose up）。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SegmentSubjectError(
                f"分割服务响应超时（{_SEGMENT_TIMEOUT_S:.0f}秒），请重试。"
            ) from exc
        except httpx.RequestError as exc:
            raise SegmentSubjectError(f"分割服务请求失败: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:500]
            raise SegmentSubjectError(
                f"分割服务返回错误 (HTTP {response.status_code}): {detail}"
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise SegmentSubjectError("分割服务返回了无效的 JSON") from exc

        mask = payload.get("mask")
        if not mask or not isinstance(mask, str):
            raise SegmentSubjectError("分割服务未返回有效蒙版（缺少 mask 字段）")

        return mask

    def _save_mask(self, mask_data_url: str) -> Path:
        """Save the mask data URL to a temporary PNG file under media/masks/."""
        raw, _ = decode_image_data_url(mask_data_url)
        masks_dir = ensure_dir(get_media_dir() / "masks")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mask_path = masks_dir / f"mask_{timestamp}_{uuid.uuid4().hex[:6]}.png"
        mask_path.write_bytes(raw)
        return mask_path

    async def check_service_health(self) -> bool:
        """Check if the BiRefNet service is healthy."""
        url = f"{self._api_base}/health"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False
