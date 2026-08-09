"""End-to-end integration tests: tool params → provider HTTP request body.

These tests wire the real VideoGenerationTool to a real AgnesVideoGenerationClient
backed by an httpx.MockTransport, verifying the full parameter mapping chain
without any monkeypatching of the provider layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nanobot.agent.tools.video_generation import (
    VideoGenerationTool,
    VideoGenerationToolConfig,
)
from nanobot.config.schema import ProviderConfig
from nanobot.providers.video_generation import AgnesVideoGenerationClient


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
) -> httpx.Response:
    request = httpx.Request("POST", "https://apihub.agnes-ai.com/v1/videos")
    if json_data is not None:
        return httpx.Response(status_code, json=json_data, request=request)
    return httpx.Response(status_code, text="", request=request)


def _make_tool_with_mock_client(
    tmp_path: Path,
    handler,
    *,
    config_overrides: dict[str, Any] | None = None,
) -> tuple[VideoGenerationTool, httpx.AsyncClient]:
    """Create a VideoGenerationTool wired to a mock Agnes API."""
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    overrides = config_overrides or {}
    config = VideoGenerationToolConfig(
        enabled=True,
        provider="agnes",
        model="agnes-video-v2.0",
        **overrides,
    )

    tool = VideoGenerationTool(
        workspace=tmp_path,
        config=config,
        provider_configs={"agnes": ProviderConfig(api_key="sk-test")},
        bus=None,
        sessions=None,
        schedule_background=None,
    )

    # Patch the provider client to use our mock httpx client
    def patched_provider_client():
        return AgnesVideoGenerationClient(
            api_key="sk-test",
            client=mock_client,
        )

    tool._provider_client = patched_provider_client  # type: ignore[method-assign]
    return tool, mock_client


@pytest.fixture(autouse=True)
def _stub_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(
        channel="test",
        chat_id="chat1",
        session_key="test:chat1",
    )
    import nanobot.agent.tools.context as ctx_mod

    monkeypatch.setattr(ctx_mod, "current_request_context", lambda: ctx)


@pytest.mark.asyncio
async def test_e2e_multi_reference_request_body(tmp_path: Path) -> None:
    """Tool params for multi_reference produce correct HTTP request body."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "vid_mr",
            "task_id": "task_mr",
            "status": "queued",
            "progress": 0,
        })

    tool, mock_client = _make_tool_with_mock_client(tmp_path, handler)
    await tool.execute(
        prompt="Showcase video",
        reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
        duration="5s",
        aspect_ratio="16:9",
    )
    await mock_client.aclose()

    assert captured["model"] == "agnes-video-v2.0"
    assert captured["prompt"] == "Showcase video"
    assert captured["mode"] == "multi_reference"
    assert captured["image"] == ["https://example.com/a.jpg", "https://example.com/b.jpg"]
    assert captured["num_frames"] == 121
    assert captured["frame_rate"] == 24
    assert captured["width"] == 1152
    assert captured["height"] == 768
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_e2e_keyframes_request_body(tmp_path: Path) -> None:
    """Tool params for keyframes produce correct HTTP request body."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "vid_kf",
            "task_id": "task_kf",
            "status": "queued",
            "progress": 0,
        })

    tool, mock_client = _make_tool_with_mock_client(tmp_path, handler)
    await tool.execute(
        prompt="Smooth transition",
        keyframe_images=["https://example.com/first.jpg", "https://example.com/last.jpg"],
        duration="3s",
        aspect_ratio="9:16",
    )
    await mock_client.aclose()

    assert captured["mode"] == "keyframes"
    assert captured["image"] == ["https://example.com/first.jpg", "https://example.com/last.jpg"]
    assert captured["num_frames"] == 81
    assert captured["width"] == 768
    assert captured["height"] == 1152
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_e2e_text_to_video_request_body(tmp_path: Path) -> None:
    """Text-to-video: no images, mode not set, extra_body not present."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "vid_t2v",
            "task_id": "task_t2v",
            "status": "queued",
            "progress": 0,
        })

    tool, mock_client = _make_tool_with_mock_client(tmp_path, handler)
    await tool.execute(prompt="A sunset over mountains")
    await mock_client.aclose()

    assert captured["model"] == "agnes-video-v2.0"
    assert "image" not in captured
    assert "mode" not in captured
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_e2e_img2vid_single_reference(tmp_path: Path) -> None:
    """Single reference image: mode omitted (not in body), image is a string.

    The API only accepts ti2vid/keyframes/multi_reference as mode.
    For single image-to-video, mode is omitted so the API auto-detects
    from the presence of the ``image`` field.
    """
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "vid_i2v",
            "task_id": "task_i2v",
            "status": "queued",
            "progress": 0,
        })

    tool, mock_client = _make_tool_with_mock_client(tmp_path, handler)
    await tool.execute(
        prompt="Animate this image",
        reference_images=["https://example.com/photo.jpg"],
        num_inference_steps=30,
    )
    await mock_client.aclose()

    assert "mode" not in captured
    assert captured["image"] == "https://example.com/photo.jpg"
    assert captured["num_inference_steps"] == 30


@pytest.mark.asyncio
async def test_e2e_reference_priority_over_keyframes(tmp_path: Path) -> None:
    """When both are passed, reference_images wins — verified at HTTP level."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "vid_prio",
            "task_id": "task_prio",
            "status": "queued",
            "progress": 0,
        })

    tool, mock_client = _make_tool_with_mock_client(tmp_path, handler)
    await tool.execute(
        prompt="test",
        reference_images=["https://example.com/r1.jpg", "https://example.com/r2.jpg"],
        keyframe_images=["https://example.com/k1.jpg", "https://example.com/k2.jpg"],
    )
    await mock_client.aclose()

    assert captured["mode"] == "multi_reference"
    assert captured["image"] == ["https://example.com/r1.jpg", "https://example.com/r2.jpg"]
