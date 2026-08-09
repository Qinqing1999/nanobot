"""Tests for the video generation tool — dedup, creation polling, and error handling."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.video_generation import (
    CheckVideoTool,
    VideoGenerationTool,
    VideoGenerationToolConfig,
    _clear_pending_video_tasks,
    _delivered_video_ids,
)
from nanobot.providers.video_generation import (
    VideoGenerationProvider,
    VideoTaskResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeVideoProvider(VideoGenerationProvider):
    """Fake provider that returns configurable responses."""

    provider_name = "fake"

    def __init__(
        self,
        *,
        create_response: VideoTaskResponse | None = None,
        create_error: Exception | None = None,
        status_response: VideoTaskResponse | None = None,
    ) -> None:
        super().__init__(api_key="fake-key")
        self._create_response = create_response
        self._create_error = create_error
        self._status_response = status_response
        self.create_call_count = 0

    async def create_task(self, **kwargs: Any) -> VideoTaskResponse:
        self.create_call_count += 1
        if self._create_error:
            raise self._create_error
        if self._create_response:
            return self._create_response
        return VideoTaskResponse(
            video_id="vid_default",
            task_id="task_default",
            status="queued",
            progress=0,
            seconds=None,
            size=None,
            created_at=None,
        )

    async def get_task_status(self, video_id: str) -> VideoTaskResponse:
        if self._status_response:
            return self._status_response
        return VideoTaskResponse(
            video_id=video_id,
            task_id="task_default",
            status="in_progress",
            progress=10,
            seconds=None,
            size=None,
            created_at=None,
        )


def _make_tool(
    *,
    provider: VideoGenerationProvider,
    bus: MagicMock | None = None,
) -> VideoGenerationTool:
    config = VideoGenerationToolConfig(enabled=True, provider="fake")

    def _noop_schedule(coro: Any) -> None:
        """Close the coroutine to avoid 'never awaited' warnings."""
        coro.close()

    tool = VideoGenerationTool(
        workspace="/tmp",
        config=config,
        provider_configs={},
        bus=bus or MagicMock(),
        sessions=None,
        schedule_background=_noop_schedule,
    )
    # Inject fake provider
    tool._provider_client = lambda: provider  # type: ignore[method-assign]
    return tool


def _request_context(
    *,
    channel: str = "websocket",
    chat_id: str = "c1",
    session_key: str | None = None,
) -> RequestContext:
    return RequestContext(
        channel=channel,
        chat_id=chat_id,
        session_key=session_key or f"{channel}:{chat_id}",
    )


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_dedup():
    """Clear the module-level dedup dict before each test."""
    _clear_pending_video_tasks()
    yield
    _clear_pending_video_tasks()


@pytest.mark.asyncio
async def test_dedup_same_params_second_call_returns_existing_task() -> None:
    """Calling execute() twice with identical params should not create a second task."""
    provider = FakeVideoProvider(
        create_response=VideoTaskResponse(
            video_id="vid_123",
            task_id="task_123",
            status="queued",
            progress=0,
            seconds=None,
            size=None,
            created_at=None,
        ),
    )
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    tool = _make_tool(provider=provider, bus=bus)
    ctx = _request_context()

    with request_context(ctx):
        result1 = await tool.execute(prompt="cute cat")

    with request_context(ctx):
        result2 = await tool.execute(prompt="cute cat")

    assert provider.create_call_count == 1
    assert "vid_123" in result1
    assert "vid_123" in result2
    assert "请勿重复调用" in result2


@pytest.mark.asyncio
async def test_dedup_different_params_creates_separate_tasks() -> None:
    """Different prompts should create separate tasks without dedup."""
    provider = FakeVideoProvider(
        create_response=VideoTaskResponse(
            video_id="vid_a",
            task_id="task_a",
            status="queued",
            progress=0,
            seconds=None,
            size=None,
            created_at=None,
        ),
    )
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    tool = _make_tool(provider=provider, bus=bus)
    ctx = _request_context()

    with request_context(ctx):
        await tool.execute(prompt="cute cat")

    with request_context(ctx):
        await tool.execute(prompt="cute dog")

    assert provider.create_call_count == 2


# ---------------------------------------------------------------------------
# Rate limit → creation polling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_error_triggers_background_creation_polling() -> None:
    """When create_task raises kind='rate_limit', execute() should return a
    non-error message and schedule a background task instead of failing."""
    from nanobot.providers.video_generation import VideoGenerationError

    provider = FakeVideoProvider(
        create_error=VideoGenerationError("rate limited", kind="rate_limit"),
    )
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    scheduled_coros: list = []

    def _capture_schedule(coro: Any) -> None:
        scheduled_coros.append(coro)
        coro.close()

    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    tool = VideoGenerationTool(
        workspace="/tmp",
        config=config,
        provider_configs={},
        bus=bus,
        sessions=None,
        schedule_background=_capture_schedule,
    )
    tool._provider_client = lambda: provider  # type: ignore[method-assign]

    ctx = _request_context()
    with request_context(ctx):
        result = await tool.execute(prompt="cute cat")

    # Should NOT be a ToolResult.error — should be a plain string
    assert not isinstance(result, ToolResult)
    assert "正在后台持续重试" in result
    # A background task should have been scheduled
    assert len(scheduled_coros) == 1


@pytest.mark.asyncio
async def test_creation_polling_succeeds_pushes_start_notification(monkeypatch) -> None:
    """Creation polling should eventually succeed, push start notification, and
    enter status polling."""
    from nanobot.providers.video_generation import VideoGenerationError

    # Speed up polling
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation._VIDEO_CREATE_POLL_INTERVAL_S", 0.01
    )

    call_count = 0

    class FlakyProvider(VideoGenerationProvider):
        provider_name = "flaky"

        def __init__(self) -> None:
            super().__init__(api_key="fake-key")

        async def create_task(self, **kwargs: Any) -> VideoTaskResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise VideoGenerationError("rate limited", kind="rate_limit")
            return VideoTaskResponse(
                video_id="vid_success",
                task_id="task_success",
                status="queued",
                progress=0,
                seconds=None,
                size=None,
                created_at=None,
            )

        async def get_task_status(self, video_id: str) -> VideoTaskResponse:
            return VideoTaskResponse(
                video_id=video_id, task_id="t", status="in_progress",
                progress=10, seconds=None, size=None, created_at=None,
            )

    provider = FlakyProvider()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    # Mock _poll_and_deliver so we don't enter the 10s sleep
    poll_called = False

    async def _mock_poll(**kwargs: Any) -> None:
        nonlocal poll_called
        poll_called = True

    bg_task: asyncio.Future | None = None

    def _run_background(coro: Any) -> None:
        nonlocal bg_task
        bg_task = asyncio.ensure_future(coro)

    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    tool = VideoGenerationTool(
        workspace="/tmp", config=config, provider_configs={},
        bus=bus, sessions=None, schedule_background=_run_background,
    )
    tool._provider_client = lambda: provider  # type: ignore[method-assign]
    tool._poll_and_deliver = _mock_poll  # type: ignore[method-assign]

    ctx = _request_context()
    with request_context(ctx):
        result = await tool.execute(prompt="cute cat")

    assert "正在后台持续重试" in result

    # Wait for background task to complete
    assert bg_task is not None
    await asyncio.wait_for(bg_task, timeout=5.0)

    # Should have pushed a start notification with video_id
    outbound_calls = bus.publish_outbound.call_args_list
    start_notification = [
        c for c in outbound_calls
        if "vid_success" in str(c) and "🎬" in str(c)
    ]
    assert len(start_notification) == 1
    assert poll_called, "_poll_and_deliver should have been called"


@pytest.mark.asyncio
async def test_creation_polling_timeout_pushes_failure_message(monkeypatch) -> None:
    """Creation polling should push a failure message when it times out."""
    from nanobot.providers.video_generation import VideoGenerationError

    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation._VIDEO_CREATE_POLL_INTERVAL_S", 0.01
    )
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation._VIDEO_CREATE_POLL_TIMEOUT_S", 0.05
    )

    class AlwaysRateLimitedProvider(VideoGenerationProvider):
        provider_name = "always_rl"

        def __init__(self) -> None:
            super().__init__(api_key="fake-key")

        async def create_task(self, **kwargs: Any) -> VideoTaskResponse:
            raise VideoGenerationError("rate limited", kind="rate_limit")

        async def get_task_status(self, video_id: str) -> VideoTaskResponse:
            raise NotImplementedError

    provider = AlwaysRateLimitedProvider()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    bg_task: asyncio.Future | None = None

    def _run_background(coro: Any) -> None:
        nonlocal bg_task
        bg_task = asyncio.ensure_future(coro)

    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    tool = VideoGenerationTool(
        workspace="/tmp", config=config, provider_configs={},
        bus=bus, sessions=None, schedule_background=_run_background,
    )
    tool._provider_client = lambda: provider  # type: ignore[method-assign]

    ctx = _request_context()
    with request_context(ctx):
        result = await tool.execute(prompt="cute cat")

    assert "正在后台持续重试" in result

    assert bg_task is not None
    await asyncio.wait_for(bg_task, timeout=5.0)

    # Should have pushed a timeout failure message
    outbound_calls = bus.publish_outbound.call_args_list
    timeout_msg = [
        c for c in outbound_calls
        if "视频创建失败" in str(c) and "3 分钟" in str(c)
    ]
    assert len(timeout_msg) == 1


@pytest.mark.asyncio
async def test_quota_exhaustion_pushes_outbound_and_returns_error() -> None:
    """When create_task raises kind='quota_exhausted', execute() should push
    an OutboundMessage to the user and return a ToolResult.error to the LLM."""
    from nanobot.providers.video_generation import VideoGenerationError

    provider = FakeVideoProvider(
        create_error=VideoGenerationError(
            "insufficient quota", kind="quota_exhausted",
        ),
    )
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    tool = _make_tool(provider=provider, bus=bus)
    ctx = _request_context()

    with request_context(ctx):
        result = await tool.execute(prompt="cute cat")

    assert isinstance(result, ToolResult)
    assert "quota" in str(result).lower() or "配额" in str(result)
    # Should have pushed an OutboundMessage to the user
    bus.publish_outbound.assert_called_once()
    outbound = bus.publish_outbound.call_args
    assert "视频创建失败" in str(outbound) or "quota" in str(outbound).lower()


# ---------------------------------------------------------------------------
# _infer_mode tests
# ---------------------------------------------------------------------------

class TestInferMode:
    """Tests for VideoGenerationTool._infer_mode static method."""

    def test_no_images_returns_none_none(self):
        """No images → text-to-video (mode=None, image=None)."""
        mode, image = VideoGenerationTool._infer_mode(None, None)
        assert mode is None
        assert image is None

    def test_no_images_empty_lists_returns_none_none(self):
        """Empty lists → text-to-video."""
        mode, image = VideoGenerationTool._infer_mode([], [])
        assert mode is None
        assert image is None

    def test_single_reference_image_returns_none_mode(self):
        """Single reference image → mode omitted (None), image as str.

        The API only accepts ti2vid, keyframes, multi_reference as mode.
        For single image-to-video, mode must be omitted so the API
        auto-detects from the ``image`` field.
        """
        mode, image = VideoGenerationTool._infer_mode(["img1.png"], None)
        assert mode is None
        assert image == "img1.png"

    def test_multiple_reference_images_returns_multi_reference(self):
        """Multiple reference images → multi_reference mode, image as list."""
        mode, image = VideoGenerationTool._infer_mode(["img1.png", "img2.png"], None)
        assert mode == "multi_reference"
        assert image == ["img1.png", "img2.png"]

    def test_keyframe_images_returns_keyframes(self):
        """Keyframe images → keyframes mode, image as list."""
        mode, image = VideoGenerationTool._infer_mode(None, ["kf1.png", "kf2.png"])
        assert mode == "keyframes"
        assert image == ["kf1.png", "kf2.png"]

    def test_reference_images_take_priority_over_keyframes(self):
        """When both are provided, reference_images takes priority."""
        mode, image = VideoGenerationTool._infer_mode(["ref.png"], ["kf1.png", "kf2.png"])
        assert mode is None
        assert image == "ref.png"

    def test_three_reference_images_returns_multi_reference(self):
        """3 reference images → multi_reference mode."""
        mode, image = VideoGenerationTool._infer_mode(
            ["img1.png", "img2.png", "img3.png"], None
        )
        assert mode == "multi_reference"
        assert image == ["img1.png", "img2.png", "img3.png"]


# ---------------------------------------------------------------------------
# _local_path_to_base64 tests
# ---------------------------------------------------------------------------


class TestLocalPathToBase64:
    """Tests for VideoGenerationTool._local_path_to_base64 static method."""

    def test_returns_pure_base64_without_data_prefix(self, tmp_path):
        """Output must NOT contain a ``data:`` prefix (causes Agnes API error)."""
        import base64

        img = tmp_path / "test.png"
        raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # minimal PNG header
        img.write_bytes(raw)

        result = VideoGenerationTool._local_path_to_base64(str(img))

        # Must NOT start with "data:" — this is the root cause of the bug
        assert not result.startswith("data:")
        # Must be valid base64 that decodes back to original bytes
        assert base64.b64decode(result) == raw

    def test_returns_ascii_string(self, tmp_path):
        """Output must be a pure ASCII string (safe for JSON)."""
        img = tmp_path / "test.bin"
        img.write_bytes(b"\x00\x01\x02\x03")
        result = VideoGenerationTool._local_path_to_base64(str(img))
        assert isinstance(result, str)
        result.encode("ascii")  # raises if not ASCII


# ---------------------------------------------------------------------------
# _resolve_artifact_id tests
# ---------------------------------------------------------------------------


class TestResolveArtifactId:
    """Tests for VideoGenerationTool._resolve_artifact_id."""

    def test_http_url_returned_as_is(self):
        """HTTP URLs should be returned unchanged."""
        tool = _make_tool(provider=FakeVideoProvider())
        assert tool._resolve_artifact_id("https://example.com/img.jpg") == "https://example.com/img.jpg"
        assert tool._resolve_artifact_id("http://example.com/img.jpg") == "http://example.com/img.jpg"

    def test_unresolvable_value_returned_as_is(self):
        """Values that are not URLs and can't be resolved as artifact IDs are returned as-is."""
        tool = _make_tool(provider=FakeVideoProvider())
        # No session available → returns the value unchanged
        assert tool._resolve_artifact_id("some_random_id") == "some_random_id"

    def test_artifact_id_resolved_to_pure_base64(self, tmp_path):
        """Artifact IDs should resolve to pure base64 (no data: prefix)."""
        import base64

        # Create a fake image file
        img_path = tmp_path / "generated.png"
        raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        img_path.write_bytes(raw)

        # Create a fake session with artifact registry
        fake_session = MagicMock()
        fake_session.artifact_registry.resolve_path.return_value = str(img_path)

        fake_sessions = MagicMock()
        fake_sessions.get_or_create.return_value = fake_session

        config = VideoGenerationToolConfig(enabled=True, provider="fake")
        tool = VideoGenerationTool(
            workspace="/tmp",
            config=config,
            provider_configs={},
            bus=MagicMock(),
            sessions=fake_sessions,
            schedule_background=lambda coro: coro.close(),
        )

        # Mock the request context
        ctx = RequestContext(
            channel="test",
            chat_id="c1",
            session_key="test:c1",
        )
        with request_context(ctx):
            result = tool._resolve_artifact_id("img_12345")

        # Must be pure base64 — NOT a data URL
        assert not result.startswith("data:")
        assert base64.b64decode(result) == raw
        fake_session.artifact_registry.resolve_path.assert_called_once_with("img_12345")

    def test_file_path_resolved_to_pure_base64(self, tmp_path):
        """Direct file paths (e.g. from generate_image tool result) should be
        converted to pure base64 even when not registered as artifact IDs."""
        import base64

        # Create a fake image file
        img_path = tmp_path / "img_abc123.png"
        raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        img_path.write_bytes(raw)

        # Tool with sessions=None — artifact ID lookup will be skipped,
        # but file path fallback should still work
        config = VideoGenerationToolConfig(enabled=True, provider="fake")
        tool = VideoGenerationTool(
            workspace="/tmp",
            config=config,
            provider_configs={},
            bus=MagicMock(),
            sessions=None,
            schedule_background=lambda coro: coro.close(),
        )

        result = tool._resolve_artifact_id(str(img_path))

        # Must be pure base64 — NOT a data URL and NOT the original path
        assert not result.startswith("data:")
        assert result != str(img_path)
        assert base64.b64decode(result) == raw


# ---------------------------------------------------------------------------
# Delivery dedup tests — shared _delivered_video_ids between tools
# ---------------------------------------------------------------------------


def _completed_status() -> VideoTaskResponse:
    """A completed video status with a download URL."""
    return VideoTaskResponse(
        video_id="vid_done",
        task_id="task_done",
        status="completed",
        progress=100,
        seconds="3.4",
        size="1088x832",
        created_at=None,
        video_url="https://cdn.example.com/video.mp4",
    )


def _make_check_tool(
    *,
       provider: VideoGenerationProvider,
       bus: MagicMock | None = None,
) -> CheckVideoTool:
    """Create a CheckVideoTool with a fake provider."""
    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    tool = CheckVideoTool(
        config=config,
        provider_configs={},
        bus=bus or MagicMock(),
        sessions=None,
    )
    tool._provider_client = lambda: provider  # type: ignore[method-assign]
    return tool


@pytest.mark.asyncio
async def test_check_video_skips_delivery_when_already_delivered() -> None:
    """CheckVideoTool should NOT re-deliver a video that the background
    polling already pushed to the user."""
    provider = FakeVideoProvider(status_response=_completed_status())
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    # Simulate: background polling already delivered this video
    _delivered_video_ids.add("vid_done")

    tool = _make_check_tool(provider=provider, bus=bus)
    ctx = _request_context()

    with request_context(ctx):
        result = await tool.execute(video_id="vid_done")

    # Should report status but NOT push another "视频生成完成" message
    assert "completed" in result
    assert "已下载并推送" not in result
    # No outbound messages should have been published
    bus.publish_outbound.assert_not_called()


@pytest.mark.asyncio
async def test_poll_skips_delivery_when_already_delivered(monkeypatch) -> None:
    """VideoGenerationTool._poll_and_deliver should NOT re-deliver a video
    that CheckVideoTool already pushed to the user."""
    # Speed up polling
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation._VIDEO_POLL_INTERVAL_S", 0.01
    )

    provider = FakeVideoProvider(status_response=_completed_status())
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    tool = VideoGenerationTool(
        workspace="/tmp",
        config=config,
        provider_configs={},
        bus=bus,
        sessions=None,
        schedule_background=lambda coro: coro.close(),
    )
    tool._provider_client = lambda: provider  # type: ignore[method-assign]

    # Simulate: CheckVideoTool already delivered this video
    _delivered_video_ids.add("vid_done")

    await tool._poll_and_deliver(
        video_id="vid_done",
        client=provider,
        prompt="test",
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
    )

    # No completion message should have been published — the polling
    # should have skipped delivery after seeing the video already delivered.
    # (A progress notification may still be sent, which is fine.)
    outbound_calls = bus.publish_outbound.call_args_list
    completion_msgs = [c for c in outbound_calls if "视频生成完成" in str(c)]
    assert len(completion_msgs) == 0, "Should not deliver if already delivered"


@pytest.mark.asyncio
async def test_delivery_dedup_across_both_paths(monkeypatch) -> None:
    """Integration test: background polling delivers first, then check_video
    is called — only ONE '视频生成完成' message should be pushed."""
    # Speed up polling
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation._VIDEO_POLL_INTERVAL_S", 0.01
    )

    # Mock download and storage to avoid real file I/O
    monkeypatch.setattr(
        VideoGenerationTool, "_download_video",
        staticmethod(lambda url: _async_return(b"fake_video_bytes")),
    )
    fake_artifact = {"path": "/tmp/fake.mp4", "mime": "video/mp4"}
    monkeypatch.setattr(
        "nanobot.agent.tools.video_generation.store_generated_video_artifact",
        lambda *args, **kwargs: fake_artifact,
    )

    provider = FakeVideoProvider(status_response=_completed_status())
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    # --- Step 1: background polling delivers the video ---
    config = VideoGenerationToolConfig(enabled=True, provider="fake")
    gen_tool = VideoGenerationTool(
        workspace="/tmp",
        config=config,
        provider_configs={},
        bus=bus,
        sessions=None,
        schedule_background=lambda coro: coro.close(),
    )
    gen_tool._provider_client = lambda: provider  # type: ignore[method-assign]

    await gen_tool._poll_and_deliver(
        video_id="vid_done",
        client=provider,
        prompt="test",
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
    )

    # Should have pushed exactly: 1× progress (100%) + 1× completion = 2
    outbound_calls = bus.publish_outbound.call_args_list
    completion_msgs = [c for c in outbound_calls if "视频生成完成" in str(c)]
    assert len(completion_msgs) == 1, "Background polling should deliver once"

    # --- Step 2: LLM calls check_video with the same video_id ---
    check_tool = _make_check_tool(provider=provider, bus=bus)
    ctx = _request_context()

    with request_context(ctx):
        result = await check_tool.execute(video_id="vid_done")

    # Should report status but NOT push another completion message
    assert "completed" in result
    assert "已下载并推送" not in result

    # Total completion messages should still be 1
    outbound_calls = bus.publish_outbound.call_args_list
    completion_msgs = [c for c in outbound_calls if "视频生成完成" in str(c)]
    assert len(completion_msgs) == 1, "check_video should NOT deliver again"


async def _async_return(value: Any) -> Any:
    """Helper: return a value from an async context."""
    return value
