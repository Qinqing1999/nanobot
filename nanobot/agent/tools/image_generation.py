"""Image generation tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger
from pydantic import Field

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.schema import (
    ArraySchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD,
    InboundMessage,
    OutboundMessage,
)
from nanobot.bus.queue import MessageBus
from nanobot.config.paths import get_media_dir
from nanobot.config_base import Base
from nanobot.providers.image_generation import (
    ImageGenerationError,
    ImageGenerationProvider,
    get_image_gen_provider,
    image_gen_provider_configs,
)
from nanobot.security.workspace_access import current_tool_workspace
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from nanobot.utils.artifacts import (
    ArtifactError,
    generated_image_tool_result,
    store_generated_image_artifact,
)
from nanobot.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import ProviderConfig


class ImageGenerationToolConfig(Base):
    """Image generation tool configuration."""
    enabled: bool = False
    provider: str = "openrouter"
    model: str = "openai/gpt-5.4-image-2"
    default_aspect_ratio: str = "1:1"
    default_image_size: str = "1K"
    max_images_per_turn: int = Field(default=4, ge=1, le=8)
    save_dir: str = "generated"
    segmentation_api_base: str = ""  # BiRefNet segmentation service URL (e.g. http://localhost:8001)


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "Detailed image generation or edit prompt. Include style, subject, composition, colors, and constraints.",
            min_length=1,
        ),
        reference_images=ArraySchema(
            StringSchema("Local path of an existing image artifact or user-provided image to use as an edit reference."),
            description="Optional local image paths. Use generated artifact paths for iterative edits.",
        ),
        aspect_ratio=StringSchema(
            "Optional output aspect ratio, e.g. 1:1, 16:9, 9:16, 4:3.",
        ),
        image_size=StringSchema(
            "Optional output size hint supported by the configured provider, e.g. 1K, 2K, 4K, or 1024x1024.",
        ),
        required=["prompt"],
    )
)
class ImageGenerationTool(Tool):
    """Generate persistent image artifacts through the configured image provider."""

    config_key = "image_generation"

    @classmethod
    def config_cls(cls):
        return ImageGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.image_generation.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.image_generation,
            provider_configs=ctx.image_generation_provider_configs,
            sessions=ctx.sessions,
            bus=ctx.bus,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: ImageGenerationToolConfig,
        provider_config: ProviderConfig | None = None,
        provider_configs: dict[str, ProviderConfig] | None = None,
        sessions: Any = None,
        bus: MessageBus | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})
        self._sessions = sessions
        self._bus = bus
        if provider_config is not None and "openrouter" not in self.provider_configs:
            self.provider_configs["openrouter"] = provider_config

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate or edit images and store them as persistent artifacts. "
            "Returns artifact ids and local paths. For edits, pass prior generated image paths "
            "or user image paths as reference_images. "
            "IMPORTANT: Do NOT call this tool just because the user uploaded an image. "
            "Only call when the user explicitly asks to generate, create, draw, or edit an image. "
            "If the user sends only an image without instructions, ask what they want to do with it."
        )

    def _provider_config(self) -> ProviderConfig | None:
        return self.provider_configs.get(self.config.provider)

    def _provider_client(self) -> ImageGenerationProvider | None:
        provider = self._provider_config()
        cls = get_image_gen_provider(self.config.provider)
        if cls is None:
            return None
        kwargs: dict[str, Any] = {
            "api_key": provider.api_key if provider and isinstance(provider.api_key, str) else None,
            "api_base": provider.api_base if provider and isinstance(provider.api_base, str) else None,
            "extra_headers": provider.extra_headers
            if provider and isinstance(provider.extra_headers, dict) else None,
            "extra_body": provider.extra_body
            if provider and isinstance(provider.extra_body, dict) else None,
            "proxy": provider.proxy if provider and isinstance(provider.proxy, str) else None,
        }
        return cls(**kwargs)

    def _resolve_reference_image(self, value: str) -> str:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                value,
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise ImageGenerationError(
                "reference_images must be inside the workspace or nanobot media directory"
            ) from exc
        except OSError as exc:
            raise ImageGenerationError(f"reference image not found: {value}") from exc
        if not resolved.is_file():
            raise ImageGenerationError(f"reference image is not a file: {value}")
        raw = resolved.read_bytes()
        if detect_image_mime(raw) is None:
            raise ImageGenerationError(f"unsupported reference image: {value}")
        return str(resolved)

    def _resolve_reference_images(self, values: list[str] | None) -> list[str]:
        if not values:
            return []
        return [self._resolve_reference_image(value) for value in values if value]

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
                model=self.config.model,
            )
            artifact["artifact_id"] = entry.id
            session.sync_artifact_registry()
            self._sessions.save(session)
        except Exception:
            logger.debug("Failed to register image artifact in session registry")

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        prompt: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return ToolResult.error(f"Error: unsupported image generation provider '{self.config.provider}'")

        try:
            refs = self._resolve_reference_images(reference_images)
            artifacts: list[dict[str, Any]] = []
            response = await client.generate(
                prompt=prompt,
                model=self.config.model,
                reference_images=refs,
                aspect_ratio=aspect_ratio or self.config.default_aspect_ratio,
                image_size=image_size or self.config.default_image_size,
            )
            for image_data_url in response.images:
                artifact = store_generated_image_artifact(
                    image_data_url,
                    prompt=prompt,
                    model=self.config.model,
                    source_images=refs,
                    save_dir=self.config.save_dir,
                    provider=self.config.provider,
                )
                # Register in session artifact registry
                self._register_artifact(artifact, prompt=prompt)
                artifacts.append(artifact)
            # Push artifact notification to user (before returning result to LLM)
            if self._bus and artifacts:
                from nanobot.agent.tools.context import current_request_context
                from nanobot.utils.artifact_registry import format_artifact_notification

                req_ctx = current_request_context()
                if req_ctx is not None:
                    notification_artifacts = [
                        {
                            "id": a.get("artifact_id", ""),
                            "type": "image",
                            "filename": Path(a.get("path", "")).name,
                        }
                        for a in artifacts
                        if a.get("artifact_id")
                    ]
                    if notification_artifacts:
                        notification = format_artifact_notification(notification_artifacts)
                        await self._bus.publish_outbound(OutboundMessage(
                            channel=req_ctx.channel,
                            chat_id=req_ctx.chat_id,
                            content=notification,
                        ))
            return generated_image_tool_result(artifacts)
        except (ArtifactError, ImageGenerationError, OSError) as exc:
            return ToolResult.error(f"Error: {exc}")


async def reload_image_generation_tool(state: Any, registry: ToolRegistry) -> dict[str, Any]:
    """Apply the persisted image configuration to the running agent."""
    try:
        from nanobot.config.loader import load_config, resolve_config_env_vars

        config = resolve_config_env_vars(load_config())
        tool_config = config.tools.image_generation
        provider_configs = image_gen_provider_configs(config)
    except Exception as exc:
        logger.warning("Image generation hot reload could not read config: {}", exc)
        return {
            "ok": False,
            "message": "Could not reload image generation config.",
            "requires_restart": True,
            "error": str(exc),
        }

    next_tool = (
        ImageGenerationTool(  # pyright: ignore[reportAbstractUsage]
            workspace=state.workspace,
            config=tool_config,
            provider_configs=provider_configs,
            sessions=getattr(state, "sessions", None),
            bus=getattr(state, "bus", None),
        )
        if tool_config.enabled
        else None
    )

    state.tools_config.image_generation = tool_config
    state._image_generation_provider_configs = provider_configs
    if next_tool is not None:
        registry.register(next_tool)
    else:
        registry.unregister("generate_image")

    logger.info(
        "Image generation config reloaded: enabled={} provider={} model={}",
        tool_config.enabled,
        tool_config.provider,
        tool_config.model,
    )
    return {
        "ok": True,
        "message": "Image generation settings applied without restarting nanobot.",
        "enabled": tool_config.enabled,
        "provider": tool_config.provider,
        "model": tool_config.model,
        "requires_restart": False,
    }


async def request_image_generation_reload(
    bus: MessageBus,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Ask the running agent loop to refresh its image generation tool."""
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-settings",
            chat_id="runtime",
            content=RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "message": "Image generation hot reload timed out.",
            "requires_restart": True,
        }
    if not isinstance(cast(object, result), dict):
        return {
            "ok": False,
            "message": "Image generation hot reload returned an unexpected response.",
            "requires_restart": True,
        }
    return result


async def handle_runtime_control(
    state: Any,
    msg: InboundMessage,
    registry: ToolRegistry,
) -> bool:
    """Handle an in-process image generation reload request."""
    metadata = msg.metadata
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_IMAGE_GENERATION_RELOAD:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    try:
        result = await reload_image_generation_tool(state, registry)
    except Exception as exc:
        logger.exception("Image generation hot reload failed")
        result = {
            "ok": False,
            "message": "Image generation hot reload failed.",
            "requires_restart": True,
            "error": str(exc),
        }
    if isinstance(ack, asyncio.Future) and not ack.done():
        cast(asyncio.Future[Any], ack).set_result(result)
    return True
