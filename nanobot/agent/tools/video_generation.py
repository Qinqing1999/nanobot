"""Video generation tool — Agnes Video V2.0 with background polling."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OutboundMessage
from nanobot.config_base import Base
from nanobot.providers.video_generation import (
    VideoGenerationError,
    VideoGenerationProvider,
    get_video_gen_provider,
    video_gen_provider_configs,
)
from nanobot.utils.artifacts import store_generated_video_artifact

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import ProviderConfig
    from nanobot.session.manager import SessionManager

_VIDEO_POLL_INTERVAL_S = 10.0
_VIDEO_POLL_MAX_DURATION_S = 300.0  # 5 minutes
_VIDEO_CREATE_POLL_INTERVAL_S = 15.0
_VIDEO_CREATE_POLL_TIMEOUT_S = 180.0  # 3 minutes

# ---------------------------------------------------------------------------
# Pending video task tracking (dedup)
# ---------------------------------------------------------------------------


@dataclass
class _PendingTask:
    """Tracks a video task from creation attempt to terminal state."""

    state: str  # "creating" | "polling"
    video_id: str | None = None
    channel: str = ""
    chat_id: str = ""


# Module-level dict: dedup_key → _PendingTask.
# Process-lifetime only (restart = lost), consistent with ADR-0002.
_pending_video_tasks: dict[str, _PendingTask] = {}

# Module-level set: video_ids that have already been delivered to the user.
# Shared between VideoGenerationTool._poll_and_deliver (background polling)
# and CheckVideoTool._deliver_completed_video (fallback delivery) to prevent
# the same completed video from being downloaded, stored, and pushed twice.
_delivered_video_ids: set[str] = set()


def _clear_pending_video_tasks() -> None:
    """Clear all pending video task entries. For testing only."""
    _pending_video_tasks.clear()
    _delivered_video_ids.clear()


# Duration presets: (num_frames, frame_rate)
_DURATION_PRESETS: dict[str, tuple[int, int]] = {
    "3s": (81, 24),
    "5s": (121, 24),
    "10s": (241, 24),
    "18s": (441, 24),
}

# Aspect ratio → (width, height)
_ASPECT_RATIO_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (1152, 768),
    "9:16": (768, 1152),
    "1:1": (768, 768),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


class VideoGenerationToolConfig(Base):
    """Video generation tool configuration."""

    enabled: bool = False
    provider: str = "agnes"
    model: str = "agnes-video-v2.0"
    save_dir: str = "generated"
    default_width: int = 1152
    default_height: int = 768
    default_aspect_ratio: str = "16:9"
    default_num_frames: int = 121
    default_frame_rate: int = 24


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "视频内容描述。包含场景、主体、动作、镜头运动、光线等。",
            min_length=1,
        ),
        reference_images=ArraySchema(
            StringSchema("参考图片的制品 ID（四位数字，如 1021）或图片 URL"),
            description=(
                "参考图片，支持 1-4 张。1 张为单图生视频，2-4 张为多图参考模式。\n"
                "传入之前 generate_image 返回的 artifact_id（四位数字，如 \"1021\"）。\n"
                "当用户要求用图片生成视频时，此参数为必填——不传则只会生成纯文字视频。"
            ),
            nullable=True,
        ),
        keyframe_images=ArraySchema(
            StringSchema("关键帧图片 artifact ID 或 URL"),
            description=(
                "关键帧动画模式的输入图片，恰好 2 张：首帧和尾帧。"
                "与 reference_images 互斥，同时传入时 reference_images 优先。"
            ),
            nullable=True,
        ),
        mode=StringSchema(
            "生成模式，由图片参数自动推断，无需手动指定。"
            "无图时为文生视频，单图为图生视频，多图为关键帧动画。",
            nullable=True,
        ),
        aspect_ratio=StringSchema(
            "宽高比: 16:9 (横版), 9:16 (竖版短视频), 1:1 (方形), 4:3, 3:4。",
            nullable=True,
        ),
        duration=StringSchema(
            "目标时长: 3s, 5s, 10s, 18s。自动设置 num_frames 和 frame_rate。",
            nullable=True,
        ),
        num_frames=IntegerSchema(
            description="直接指定帧数，覆盖 duration 预设。",
            nullable=True,
        ),
        frame_rate=IntegerSchema(
            description="直接指定帧率，覆盖 duration 预设。",
            nullable=True,
        ),
        num_inference_steps=IntegerSchema(
            description="推理步数，控制生成质量和速度的平衡。",
            nullable=True,
        ),
        negative_prompt=StringSchema(
            "反向提示词，描述需要避免的内容。",
            nullable=True,
        ),
        seed=IntegerSchema(
            description="随机种子，用于生成可复现结果。",
            nullable=True,
        ),
        required=["prompt"],
    )
)
class VideoGenerationTool(Tool):
    """Generate videos through Agnes Video V2.0 with background polling."""

    config_key = "video_generation"

    @classmethod
    def config_cls(cls) -> type[VideoGenerationToolConfig]:
        return VideoGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.video_generation.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        from nanobot.config.loader import load_config

        try:
            full_config = load_config()
            provider_configs = video_gen_provider_configs(full_config)
        except Exception:
            provider_configs = {}

        return cls(
            workspace=ctx.workspace,
            config=ctx.config.video_generation,
            provider_configs=provider_configs,
            bus=ctx.bus,
            sessions=ctx.sessions,
            schedule_background=ctx.schedule_background,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: VideoGenerationToolConfig,
        provider_configs: dict[str, ProviderConfig] | None = None,
        bus: MessageBus | None = None,
        sessions: SessionManager | None = None,
        schedule_background: Any = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})
        self.bus = bus
        self.sessions = sessions
        self._schedule_background = schedule_background

    @property
    def name(self) -> str:
        return "generate_video"

    @property
    def description(self) -> str:
        return (
            "通过文本提示词或图片生成视频。支持文生视频、图生视频、多图参考和关键帧动画。"
            "创建任务后在后台轮询，完成时自动推送视频文件给用户。\n"
            "图生视频：当用户要求用图片/照片生成视频时，必须把图片的制品 ID（四位数字，如 1021）"
            "传入 reference_images 参数。制品 ID 来自之前 generate_image 的返回结果或会话上下文。\n"
            "重要：不要仅因用户上传了图片就自动调用此工具。"
            "只有当用户明确要求生成或制作视频时才调用。"
            "如果用户只发了图片没有文字指令，先询问用户想做什么。"
        )

    def _provider_client(self) -> VideoGenerationProvider | None:
        provider = self.provider_configs.get(self.config.provider)
        cls = get_video_gen_provider(self.config.provider)
        if cls is None:
            return None
        kwargs: dict[str, Any] = {
            "api_key": provider.api_key if provider and isinstance(provider.api_key, str) else None,
            "api_base": provider.api_base if provider and isinstance(provider.api_base, str) else None,
            "extra_headers": provider.extra_headers if provider and isinstance(provider.extra_headers, dict) else None,
            "extra_body": provider.extra_body if provider and isinstance(provider.extra_body, dict) else None,
            "proxy": provider.proxy if provider and isinstance(provider.proxy, str) else None,
        }
        return cls(**kwargs)

    def _resolve_artifact_id(self, value: str) -> str:
        """Resolve an artifact ID, file path, or URL to an API-ready value.

        Accepts:
        - HTTP(S) URLs → returned as-is
        - Four-digit numeric artifact IDs (e.g. ``1020``) → resolved via
          session artifact registry to a local file path, then converted to
          pure base64
        - Local file paths (absolute or relative) → read and converted to
          pure base64

        For local files, returns **pure base64** data (without the ``data:``
        prefix) because the Agnes Video API rejects data URLs with the error
        ``image base64 decode failed: Only base64 data is allowed``.
        """
        if value.startswith(("http://", "https://")):
            return value

        # Try as four-digit numeric artifact ID first
        from nanobot.agent.tools.context import current_request_context

        request_ctx = current_request_context()
        if request_ctx and request_ctx.session_key:
            sessions = self.sessions
            if sessions is not None:
                session = sessions.get_or_create(request_ctx.session_key)
                path = session.artifact_registry.resolve_path(value)
                if path:
                    return self._local_path_to_base64(path)

        # Try as a local file path (LLM may pass the path directly)
        try:
            p = Path(value).expanduser()
            if p.is_file():
                return self._local_path_to_base64(str(p))
        except (OSError, ValueError):
            pass

        # If nothing matched, return as-is (will likely fail at the API,
        # but we let the API provide a specific error message)
        return value

    @staticmethod
    def _local_path_to_base64(path: str) -> str:
        """Convert a local file to **pure base64** data for API submission.

        The Agnes Video API expects either a publicly accessible URL or raw
        base64 data (without a ``data:`` prefix).  Data URLs cause a
        ``image base64 decode failed`` error.
        """
        import base64

        raw = Path(path).read_bytes()
        return base64.b64encode(raw).decode("ascii")

    def _resolve_duration(
        self,
        duration: str | None,
        num_frames_override: int | None = None,
        frame_rate_override: int | None = None,
    ) -> tuple[int, int]:
        preset_frames, preset_rate = (
            _DURATION_PRESETS[duration]
            if duration and duration in _DURATION_PRESETS
            else (self.config.default_num_frames, self.config.default_frame_rate)
        )
        final_frames = num_frames_override if num_frames_override is not None else preset_frames
        final_rate = frame_rate_override if frame_rate_override is not None else preset_rate
        return (final_frames, final_rate)

    def _resolve_size(self, aspect_ratio: str | None) -> tuple[int, int]:
        ratio = aspect_ratio or self.config.default_aspect_ratio
        if ratio in _ASPECT_RATIO_SIZES:
            return _ASPECT_RATIO_SIZES[ratio]
        return (self.config.default_width, self.config.default_height)

    @staticmethod
    def _infer_mode(
        reference_images: list[str] | None,
        keyframe_images: list[str] | None,
    ) -> tuple[str | None, str | list[str] | None]:
        """Infer mode and image value from the image parameters.

        Returns (mode, image_value) where image_value is:
        - None for text-to-video (mode omitted)
        - str for single image (mode omitted — API auto-detects from ``image``)
        - list[str] for multi-reference (mode="multi_reference")
        - list[str] for keyframes (mode="keyframes")

        Valid API modes are: ``ti2vid``, ``keyframes``, ``multi_reference``.
        ``img2vid`` is NOT accepted by the API — for single image-to-video
        the mode must be omitted so the API infers it from ``image``.
        """
        refs = list(reference_images or [])
        keyframes = list(keyframe_images or [])

        if refs:
            if len(refs) == 1:
                # Single image — omit mode; API auto-detects from image field
                return (None, refs[0])
            # Multiple reference images — multi_reference mode
            return ("multi_reference", refs)

        if keyframes:
            return ("keyframes", keyframes)

        return (None, None)

    @staticmethod
    def _dedup_key(
        *,
        session_key: str,
        prompt: str,
        image_value: str | list[str] | None,
        final_mode: str | None,
        width: int,
        height: int,
        num_frames: int,
        frame_rate: int,
    ) -> str:
        """Compute a dedup key from the parameters that define content identity.

        Excludes seed, negative_prompt, num_inference_steps (affect quality /
        randomness, not content semantics). URL and file path compared as
        string literals — no content-level dedup.
        """
        image_repr = "" if image_value is None else str(image_value)
        mode_repr = final_mode or ""
        raw = (
            f"{session_key}|{prompt}|{image_repr}|{mode_repr}|"
            f"{width}x{height}|{num_frames}f{frame_rate}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        prompt: str,
        reference_images: list[str] | None = None,
        keyframe_images: list[str] | None = None,
        mode: str | None = None,
        aspect_ratio: str | None = None,
        duration: str | None = None,
        num_frames: int | None = None,
        frame_rate: int | None = None,
        num_inference_steps: int | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
        **kwargs: Any,
    ) -> str:
        from nanobot.agent.tools.context import current_request_context

        request_ctx = current_request_context()
        if request_ctx is None:
            return ToolResult.error("Error: no request context for video generation")

        client = self._provider_client()
        if client is None:
            return ToolResult.error(
                f"Error: unsupported video generation provider '{self.config.provider}'"
            )

        final_num_frames, final_frame_rate = self._resolve_duration(
            duration, num_frames, frame_rate
        )
        width, height = self._resolve_size(aspect_ratio)

        # Resolve artifact IDs / URLs for reference images and keyframe images
        resolved_refs = (
            [self._resolve_artifact_id(r) for r in reference_images]
            if reference_images
            else None
        )
        resolved_keyframes = (
            [self._resolve_artifact_id(k) for k in keyframe_images]
            if keyframe_images
            else None
        )

        # Mode auto-inference: image params override explicit mode
        inferred_mode, image_value = self._infer_mode(resolved_refs, resolved_keyframes)
        final_mode = inferred_mode  # always inferred, overrides explicit mode

        channel = request_ctx.channel
        chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{channel}:{chat_id}"

        # Dedup: check if an identical task is already in progress
        dedup_key = self._dedup_key(
            session_key=session_key,
            prompt=prompt,
            image_value=image_value,
            final_mode=final_mode,
            width=width,
            height=height,
            num_frames=final_num_frames,
            frame_rate=final_frame_rate,
        )
        existing = _pending_video_tasks.get(dedup_key)
        if existing is not None:
            if existing.state == "polling" and existing.video_id:
                return (
                    f"已有一个相同的视频任务正在生成中，"
                    f"任务 ID: {existing.video_id}，请勿重复调用。"
                )
            return "已有一个相同的视频任务正在创建中（遇到限速，后台持续重试），请勿重复调用。"

        # Register as "creating" to prevent duplicate submissions
        _pending_video_tasks[dedup_key] = _PendingTask(
            state="creating",
            channel=channel,
            chat_id=chat_id,
        )

        try:
            task = await client.create_task(
                model=self.config.model,
                prompt=prompt,
                image=image_value,
                mode=final_mode,
                width=width,
                height=height,
                num_frames=final_num_frames,
                frame_rate=final_frame_rate,
                num_inference_steps=num_inference_steps,
                seed=seed,
                negative_prompt=negative_prompt,
            )
        except VideoGenerationError as exc:
            if exc.kind == "rate_limit":
                # Schedule background creation polling — non-blocking
                coro = self._create_with_polling(
                    client=client,
                    prompt=prompt,
                    image_value=image_value,
                    final_mode=final_mode,
                    width=width,
                    height=height,
                    final_num_frames=final_num_frames,
                    final_frame_rate=final_frame_rate,
                    num_inference_steps=num_inference_steps,
                    seed=seed,
                    negative_prompt=negative_prompt,
                    channel=channel,
                    chat_id=chat_id,
                    session_key=session_key,
                    dedup_key=dedup_key,
                )
                safe_coro = self._safe_poll(coro)
                if self._schedule_background is not None:
                    self._schedule_background(safe_coro)
                else:
                    asyncio.create_task(safe_coro)
                return (
                    "遇到限速，正在后台持续重试创建，最长 3 分钟，完成后自动通知你。"
                    "请勿重复调用此工具。"
                )
            # quota_exhausted or unknown — push notification and fail
            if exc.kind == "quota_exhausted" and self.bus:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=f"❌ 视频创建失败：{exc}",
                ))
            _pending_video_tasks.pop(dedup_key, None)
            return ToolResult.error(f"Error: {exc}")

        # Task created successfully — transition to "polling" state
        _pending_video_tasks[dedup_key].state = "polling"
        _pending_video_tasks[dedup_key].video_id = task.video_id

        # Push "start" notification
        if self.bus:
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=(
                    f"🎬 视频正在生成中...\n"
                    f"**任务 ID**: `{task.video_id}`\n"
                    f"预计需要 1-5 分钟，完成后会自动发送给你。"
                ),
            ))

        # Start background polling — always wrap in _safe_poll so exceptions
        # are logged and the user gets a notification instead of silence.
        coro = self._poll_and_deliver(
            video_id=task.video_id,
            client=client,
            prompt=prompt,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            dedup_key=dedup_key,
        )
        safe_coro = self._safe_poll(coro)
        if self._schedule_background is not None:
            self._schedule_background(safe_coro)
        else:
            asyncio.create_task(safe_coro)

        return (
            f"视频任务已创建。任务 ID: {task.video_id}\n"
            f"状态: {task.status}\n"
            f"后台正在轮询，完成后会自动推送视频给你。\n"
            f"请勿调用 check_video 反复查询，后台会自动通知。"
        )

    @staticmethod
    async def _safe_poll(coro: Any) -> None:
        """Wrap a background coroutine so exceptions are logged, not lost."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Video background polling task failed")

    async def _create_with_polling(
        self,
        *,
        client: VideoGenerationProvider,
        prompt: str,
        image_value: str | list[str] | None,
        final_mode: str | None,
        width: int,
        height: int,
        final_num_frames: int,
        final_frame_rate: int,
        num_inference_steps: int | None,
        seed: int | None,
        negative_prompt: str | None,
        channel: str,
        chat_id: str,
        session_key: str,
        dedup_key: str,
    ) -> None:
        """Background creation polling — retries create_task on rate limit.

        Runs every 15s with a 3-minute wall-clock timeout. On success,
        pushes the 'start' notification and enters _poll_and_deliver.
        On timeout or quota exhaustion, pushes an OutboundMessage.
        """
        start_time = time.monotonic()

        try:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed > _VIDEO_CREATE_POLL_TIMEOUT_S:
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=(
                                "❌ 视频创建失败：持续限速 3 分钟，请稍后再试。"
                            ),
                        ))
                    return

                await asyncio.sleep(_VIDEO_CREATE_POLL_INTERVAL_S)

                try:
                    task = await client.create_task(
                        model=self.config.model,
                        prompt=prompt,
                        image=image_value,
                        mode=final_mode,
                        width=width,
                        height=height,
                        num_frames=final_num_frames,
                        frame_rate=final_frame_rate,
                        num_inference_steps=num_inference_steps,
                        seed=seed,
                        negative_prompt=negative_prompt,
                    )
                except VideoGenerationError as exc:
                    if exc.kind == "rate_limit":
                        logger.warning(
                            "Video creation still rate-limited after {:.0f}s, "
                            "will retry in {}s",
                            elapsed, _VIDEO_CREATE_POLL_INTERVAL_S,
                        )
                        continue
                    # quota_exhausted or unknown — notify and stop
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=f"❌ 视频创建失败：{exc}",
                        ))
                    return

                # Success — transition to "polling" and push start notification
                _pending_video_tasks[dedup_key].state = "polling"
                _pending_video_tasks[dedup_key].video_id = task.video_id

                if self.bus:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=(
                            f"🎬 视频正在生成中...\n"
                            f"**任务 ID**: `{task.video_id}`\n"
                            f"预计需要 1-5 分钟，完成后会自动发送给你。"
                        ),
                    ))

                # Enter status polling
                await self._poll_and_deliver(
                    video_id=task.video_id,
                    client=client,
                    prompt=prompt,
                    channel=channel,
                    chat_id=chat_id,
                    session_key=session_key,
                    dedup_key=dedup_key,
                )
                return

        except asyncio.CancelledError:
            logger.info("Video creation polling cancelled for session {}", session_key)
            raise
        finally:
            if dedup_key and _pending_video_tasks.get(dedup_key, _PendingTask(state="polling")).state == "creating":
                _pending_video_tasks.pop(dedup_key, None)

    async def _poll_and_deliver(
        self,
        *,
        video_id: str,
        client: VideoGenerationProvider,
        prompt: str,
        channel: str,
        chat_id: str,
        session_key: str,
        dedup_key: str = "",
    ) -> None:
        """Background polling task — runs until completion or timeout."""
        start_time = time.monotonic()
        fifty_percent_notified = False
        consecutive_errors = 0
        max_consecutive_errors = 10

        try:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed > _VIDEO_POLL_MAX_DURATION_S:
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=(
                                f"⚠️ 视频生成超时（{int(_VIDEO_POLL_MAX_DURATION_S)}秒），请稍后重试。\n"
                                f"**任务 ID**: `{video_id}`"
                            ),
                        ))
                    return

                await asyncio.sleep(_VIDEO_POLL_INTERVAL_S)

                try:
                    status = await client.get_task_status(video_id)
                    consecutive_errors = 0
                except VideoGenerationError as exc:
                    logger.warning("Video poll error for {}: {}", video_id, exc)
                    continue
                except httpx.HTTPStatusError as exc:
                    consecutive_errors += 1
                    if exc.response.status_code == 429:
                        # Rate limited — back off exponentially
                        backoff = min(_VIDEO_POLL_INTERVAL_S * (2 ** consecutive_errors), 60.0)
                        logger.warning(
                            "Video poll rate-limited (429) for {}, backing off {}s (attempt {})",
                            video_id, backoff, consecutive_errors,
                        )
                        if consecutive_errors >= max_consecutive_errors:
                            if self.bus:
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=channel,
                                    chat_id=chat_id,
                                    content=(
                                        f"⚠️ 视频状态查询频繁被限流，后台轮询已停止。\n"
                                        f"**任务 ID**: `{video_id}`\n"
                                        f"视频可能已生成完成，请用 /check_video {video_id} 手动查询。"
                                    ),
                                ))
                            return
                        await asyncio.sleep(backoff)
                        continue
                    logger.warning("Video poll HTTP error for {}: {}", video_id, exc)
                    continue
                except httpx.HTTPError as exc:
                    consecutive_errors += 1
                    logger.warning("Video poll network error for {}: {}", video_id, exc)
                    if consecutive_errors >= max_consecutive_errors:
                        if self.bus:
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=(
                                    f"⚠️ 视频状态查询持续失败，后台轮询已停止。\n"
                                    f"**任务 ID**: `{video_id}`\n"
                                    f"请用 /check_video {video_id} 手动查询。"
                                ),
                            ))
                        return
                    continue

                # 50% progress notification
                if not fifty_percent_notified and status.progress >= 50:
                    fifty_percent_notified = True
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=f"📊 视频生成进度: {status.progress}%",
                        ))

                if status.status == "completed" and status.video_url:
                    # Delivery dedup: if CheckVideoTool already delivered this
                    # video (fallback path), skip duplicate download/push.
                    if video_id in _delivered_video_ids:
                        logger.info("Video {} already delivered, skipping", video_id)
                        return
                    _delivered_video_ids.add(video_id)

                    # Download, store, register, and push — each step in its
                    # own try/except so a failure still notifies the user
                    # instead of silently killing the background task.
                    try:
                        video_bytes = await self._download_video(status.video_url)
                    except Exception as exc:
                        logger.error("Video download failed for {}: {}", video_id, exc)
                        _delivered_video_ids.discard(video_id)
                        if self.bus:
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=(
                                    f"✅ 视频已生成完成，但下载失败。\n"
                                    f"**任务 ID**: `{video_id}`\n"
                                    f"错误: {exc}"
                                ),
                            ))
                        return
                    try:
                        artifact = store_generated_video_artifact(
                            video_bytes,
                            prompt=prompt,
                            model=self.config.model,
                            save_dir=self.config.save_dir,
                            provider=self.config.provider,
                        )
                    except Exception as exc:
                        logger.error("Video artifact storage failed for {}: {}", video_id, exc)
                        _delivered_video_ids.discard(video_id)
                        if self.bus:
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=(
                                    f"✅ 视频已生成完成，但保存失败。\n"
                                    f"**任务 ID**: `{video_id}`\n"
                                    f"错误: {exc}"
                                ),
                            ))
                        return
                    artifact_id = ""
                    try:
                        sessions = self.sessions
                        if sessions is not None:
                            session = sessions.get_or_create(session_key)
                            entry = session.artifact_registry.register(
                                type="video",
                                path=artifact["path"],
                                filename=Path(artifact["path"]).name,
                                mime=artifact["mime"],
                                source="generated",
                                prompt=prompt,
                                model=self.config.model,
                            )
                            artifact_id = entry.id
                            session.sync_artifact_registry()
                            sessions.save(session)
                    except Exception as exc:
                        logger.error("Video artifact registration failed for {}: {}", video_id, exc)

                    # Push video file to user
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=(
                                f"✅ 视频生成完成！\n"
                                f"**时长**: `{status.seconds}秒`\n"
                                f"**分辨率**: `{status.size}`\n"
                                f"**制品 ID**: `{artifact_id}`"
                            ),
                            media=[artifact["path"]],
                        ))
                    return

                if status.status == "failed":
                    if self.bus:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel,
                            chat_id=chat_id,
                            content=(
                                f"❌ 视频生成失败。\n"
                                f"**任务 ID**: `{video_id}`\n"
                                f"错误: {status.error or '未知错误'}"
                            ),
                        ))
                    return

        except asyncio.CancelledError:
            logger.info("Video polling cancelled for {}", video_id)
            raise
        finally:
            if dedup_key:
                _pending_video_tasks.pop(dedup_key, None)

    @staticmethod
    async def _download_video(url: str) -> bytes:
        """Download video from URL."""
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content


@tool_parameters(
    tool_parameters_schema(
        video_id=StringSchema(
            "视频任务 ID",
            min_length=1,
        ),
        required=["video_id"],
    )
)
class CheckVideoTool(Tool):
    """Check video task status by video_id."""

    config_key = "video_generation"

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.video_generation.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        from nanobot.config.loader import load_config

        try:
            full_config = load_config()
            provider_configs = video_gen_provider_configs(full_config)
        except Exception:
            provider_configs = {}

        return cls(
            config=ctx.config.video_generation,
            provider_configs=provider_configs,
            bus=ctx.bus,
            sessions=ctx.sessions,
        )

    def __init__(
        self,
        *,
        config: VideoGenerationToolConfig,
        provider_configs: dict[str, ProviderConfig] | None = None,
        bus: MessageBus | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        self.config = config
        self.provider_configs = dict(provider_configs or {})
        self.bus = bus
        self.sessions = sessions

    @property
    def name(self) -> str:
        return "check_video"

    @property
    def description(self) -> str:
        return (
            "查询视频生成任务的状态。输入 video_id 返回当前状态和进度。"
            "重要：后台已自动轮询，视频完成后会自动推送给用户。"
            "请勿反复调用此工具查询进度——每次调用都会消耗 API 配额并可能触发限流。"
            "只有在用户明确询问视频状态时才调用，且每次调用后应等待用户回复，不要自动连续查询。"
        )

    def _provider_client(self) -> VideoGenerationProvider | None:
        provider = self.provider_configs.get(self.config.provider)
        cls = get_video_gen_provider(self.config.provider)
        if cls is None:
            return None
        kwargs: dict[str, Any] = {
            "api_key": provider.api_key if provider and isinstance(provider.api_key, str) else None,
            "api_base": provider.api_base if provider and isinstance(provider.api_base, str) else None,
        }
        return cls(**kwargs)

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        video_id: str,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return ToolResult.error("Video provider not configured")
        try:
            status = await client.get_task_status(video_id)
            result = (
                f"视频任务 {video_id}\n"
                f"状态: {status.status}\n"
                f"进度: {status.progress}%"
            )
            if status.error:
                result += f"\n错误: {status.error}"

            # Fallback delivery: if the video is completed and hasn't been
            # delivered yet, download and push it here.  This covers the case
            # where the background polling task failed or was rate-limited.
            # Uses the module-level _delivered_video_ids set shared with
            # VideoGenerationTool._poll_and_deliver to prevent duplicates.
            if (
                status.status == "completed"
                and status.video_url
                and video_id not in _delivered_video_ids
            ):
                _delivered_video_ids.add(video_id)
                await self._deliver_completed_video(
                    video_id=video_id,
                    video_url=status.video_url,
                    seconds=status.seconds,
                    size=status.size,
                )
                result += "\n✅ 视频已下载并推送给用户。"
            elif status.status == "completed" and not status.video_url:
                result += "\n⚠️ 视频已完成但未获取到下载链接。"
            elif status.status in ("queued", "in_progress"):
                result += (
                    "\n⏳ 视频仍在生成中，后台正在自动轮询。"
                    "完成后会自动推送给用户，请勿再次调用 check_video。"
                )

            return result
        except VideoGenerationError as exc:
            return ToolResult.error(f"Error: {exc}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                return ToolResult.error(
                    "Error: 查询过于频繁，API 返回 429。"
                    "后台正在自动轮询，视频完成后会自动推送。"
                    "请勿再次调用 check_video，请等待用户主动询问或后台通知。"
                )
            return ToolResult.error(f"Error: HTTP {exc.response.status_code}: {exc}")
        except httpx.HTTPError as exc:
            return ToolResult.error(f"Error: 网络错误: {exc}")

    async def _deliver_completed_video(
        self,
        *,
        video_id: str,
        video_url: str,
        seconds: str | None,
        size: str | None,
    ) -> None:
        """Download, store, register, and push a completed video to the user."""
        from nanobot.agent.tools.context import current_request_context

        req_ctx = current_request_context()
        if req_ctx is None:
            return

        try:
            video_bytes = await VideoGenerationTool._download_video(video_url)
        except Exception as exc:
            logger.error("Fallback video download failed for {}: {}", video_id, exc)
            _delivered_video_ids.discard(video_id)
            if self.bus:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=req_ctx.channel,
                    chat_id=req_ctx.chat_id,
                    content=(
                        f"✅ 视频已生成完成，但下载失败。\n"
                        f"**任务 ID**: `{video_id}`\n"
                        f"错误: {exc}"
                    ),
                ))
            return

        artifact = store_generated_video_artifact(
            video_bytes,
            prompt="",
            model=self.config.model,
            save_dir=self.config.save_dir,
            provider=self.config.provider,
        )

        artifact_id = ""
        if self.sessions is not None:
            try:
                session_key = req_ctx.session_key or f"{req_ctx.channel}:{req_ctx.chat_id}"
                session = self.sessions.get_or_create(session_key)
                entry = session.artifact_registry.register(
                    type="video",
                    path=artifact["path"],
                    filename=Path(artifact["path"]).name,
                    mime=artifact["mime"],
                    source="generated",
                    model=self.config.model,
                )
                artifact_id = entry.id
                session.sync_artifact_registry()
                self.sessions.save(session)
            except Exception:
                logger.debug("Failed to register video artifact in session registry")

        if self.bus:
            await self.bus.publish_outbound(OutboundMessage(
                channel=req_ctx.channel,
                chat_id=req_ctx.chat_id,
                content=(
                    f"✅ 视频生成完成！\n"
                    f"**时长**: `{seconds}秒`\n"
                    f"**分辨率**: `{size}`\n"
                    f"**制品 ID**: `{artifact_id}`"
                ),
                media=[artifact["path"]],
            ))
