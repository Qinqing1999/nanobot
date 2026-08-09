"""Tests for the VideoGenerationTool — parameter mapping and mode inference."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.tools.video_generation import (
    VideoGenerationTool,
    VideoGenerationToolConfig,
)
from nanobot.config.schema import ProviderConfig
from nanobot.providers.video_generation import VideoTaskResponse


class FakeVideoClient:
    """Fake video generation provider that captures create_task calls."""

    instances: list["FakeVideoClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        FakeVideoClient.instances.append(self)

    async def create_task(self, **kwargs: Any) -> VideoTaskResponse:
        self.calls.append(kwargs)
        return VideoTaskResponse(
            video_id="video_fake",
            task_id="task_fake",
            status="queued",
            progress=0,
            seconds=None,
            size=None,
            created_at=None,
        )

    async def get_task_status(self, video_id: str) -> VideoTaskResponse:
        return VideoTaskResponse(
            video_id=video_id,
            task_id="task_fake",
            status="in_progress",
            progress=10,
            seconds=None,
            size=None,
            created_at=None,
        )


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    FakeVideoClient.instances = []


@pytest.fixture(autouse=True)
def _patch_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation.get_video_gen_provider",
        lambda name: FakeVideoClient if name == "agnes" else None,
    )


@pytest.fixture(autouse=True)
def _stub_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the request context so execute() can proceed."""
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        channel="test",
        chat_id="chat1",
        session_key="test:chat1",
    )
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation.current_request_context",
        lambda: ctx,
        raising=False,
    )
    # Also patch the import inside execute()
    import nanobot.agent.tools.video_generation as mod

    original_execute = mod.VideoGenerationTool.execute

    async def patched_execute(self, *args, **kwargs):
        from nanobot.agent.tools import context as ctx_mod

        monkeypatch.setattr(ctx_mod, "current_request_context", lambda: ctx)
        return await original_execute(self, *args, **kwargs)

    monkeypatch.setattr(mod.VideoGenerationTool, "execute", patched_execute)


def _make_tool(tmp_path: Path, **config_overrides: Any) -> VideoGenerationTool:
    config = VideoGenerationToolConfig(
        enabled=True,
        provider="agnes",
        model="agnes-video-v2.0",
        **config_overrides,
    )
    return VideoGenerationTool(
        workspace=tmp_path,
        config=config,
        provider_configs={"agnes": ProviderConfig(api_key="sk-test")},
        bus=None,
        sessions=None,
        schedule_background=None,
    )


# -- Mode auto-inference tests ------------------------------------------------


@pytest.mark.asyncio
async def test_no_images_infers_ti2vid(tmp_path: Path) -> None:
    """Text-to-video: no image params → mode omitted (provider default)."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="sunset over ocean")
    call = FakeVideoClient.instances[0].calls[0]
    assert call.get("mode") is None
    assert call.get("image") is None


@pytest.mark.asyncio
async def test_single_reference_image_omits_mode(tmp_path: Path) -> None:
    """One reference image → mode omitted (None), image is a single string.

    The API only accepts ti2vid/keyframes/multi_reference as mode.
    For single image-to-video, mode is omitted so the API auto-detects.
    """
    tool = _make_tool(tmp_path)
    await tool.execute(
        prompt="animate this",
        reference_images=["https://example.com/img.jpg"],
    )
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] is None
    assert call["image"] == "https://example.com/img.jpg"


@pytest.mark.asyncio
async def test_two_reference_images_infers_multi_reference(tmp_path: Path) -> None:
    """Two reference images → mode=multi_reference, image is a list."""
    tool = _make_tool(tmp_path)
    await tool.execute(
        prompt="showcase from multiple angles",
        reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
    )
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] == "multi_reference"
    assert call["image"] == ["https://example.com/a.jpg", "https://example.com/b.jpg"]


@pytest.mark.asyncio
async def test_four_reference_images_infers_multi_reference(tmp_path: Path) -> None:
    """Four reference images → mode=multi_reference."""
    tool = _make_tool(tmp_path)
    urls = [f"https://example.com/{i}.jpg" for i in range(4)]
    await tool.execute(prompt="showcase", reference_images=urls)
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] == "multi_reference"
    assert call["image"] == urls


@pytest.mark.asyncio
async def test_keyframe_images_infers_keyframes(tmp_path: Path) -> None:
    """Two keyframe images → mode=keyframes, image is a list."""
    tool = _make_tool(tmp_path)
    await tool.execute(
        prompt="smooth transition",
        keyframe_images=["https://example.com/first.jpg", "https://example.com/last.jpg"],
    )
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] == "keyframes"
    assert call["image"] == ["https://example.com/first.jpg", "https://example.com/last.jpg"]


@pytest.mark.asyncio
async def test_reference_images_takes_priority_over_keyframes(tmp_path: Path) -> None:
    """When both reference_images and keyframe_images are provided, reference wins."""
    tool = _make_tool(tmp_path)
    await tool.execute(
        prompt="test",
        reference_images=["https://example.com/ref1.jpg", "https://example.com/ref2.jpg"],
        keyframe_images=["https://example.com/kf1.jpg", "https://example.com/kf2.jpg"],
    )
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] == "multi_reference"
    assert call["image"] == ["https://example.com/ref1.jpg", "https://example.com/ref2.jpg"]


@pytest.mark.asyncio
async def test_explicit_mode_overridden_by_image_params(tmp_path: Path) -> None:
    """Explicit mode is overridden by the image parameter inference."""
    tool = _make_tool(tmp_path)
    await tool.execute(
        prompt="test",
        reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
        mode="ti2vid",
    )
    call = FakeVideoClient.instances[0].calls[0]
    assert call["mode"] == "multi_reference"


# -- Duration and frame parameter tests ---------------------------------------


@pytest.mark.asyncio
async def test_duration_5s_maps_to_correct_frames(tmp_path: Path) -> None:
    """duration='5s' maps to num_frames=121, frame_rate=24."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="test", duration="5s")
    call = FakeVideoClient.instances[0].calls[0]
    assert call["num_frames"] == 121
    assert call["frame_rate"] == 24


@pytest.mark.asyncio
async def test_num_frames_overrides_duration(tmp_path: Path) -> None:
    """Explicit num_frames overrides duration preset."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="test", duration="5s", num_frames=200)
    call = FakeVideoClient.instances[0].calls[0]
    assert call["num_frames"] == 200
    assert call["frame_rate"] == 24


@pytest.mark.asyncio
async def test_frame_rate_overrides_duration(tmp_path: Path) -> None:
    """Explicit frame_rate overrides duration preset."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="test", duration="5s", frame_rate=30)
    call = FakeVideoClient.instances[0].calls[0]
    assert call["num_frames"] == 121
    assert call["frame_rate"] == 30


# -- num_inference_steps test -------------------------------------------------


@pytest.mark.asyncio
async def test_num_inference_steps_passed_through(tmp_path: Path) -> None:
    """num_inference_steps is forwarded to the provider."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="test", num_inference_steps=50)
    call = FakeVideoClient.instances[0].calls[0]
    assert call["num_inference_steps"] == 50


# -- Aspect ratio tests -------------------------------------------------------


@pytest.mark.asyncio
async def test_aspect_ratio_9_16_maps_to_correct_dimensions(tmp_path: Path) -> None:
    """aspect_ratio='9:16' → width=768, height=1152."""
    tool = _make_tool(tmp_path)
    await tool.execute(prompt="test", aspect_ratio="9:16")
    call = FakeVideoClient.instances[0].calls[0]
    assert call["width"] == 768
    assert call["height"] == 1152


@pytest.mark.asyncio
async def test_default_aspect_ratio_from_config(tmp_path: Path) -> None:
    """When aspect_ratio is not passed, config.default_aspect_ratio is used."""
    tool = _make_tool(tmp_path, default_aspect_ratio="9:16")
    await tool.execute(prompt="test")
    call = FakeVideoClient.instances[0].calls[0]
    assert call["width"] == 768
    assert call["height"] == 1152
