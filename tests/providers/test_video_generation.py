"""Tests for the Agnes Video V2.0 provider."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nanobot.providers.video_generation import (
    AgnesVideoGenerationClient,
    VideoGenerationError,
    VideoTaskResponse,
    _normalize_status,
    get_video_gen_provider,
)


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Create a mock httpx.Response."""
    request = httpx.Request("POST", "https://apihub.agnes-ai.com/v1/videos")
    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "headers": headers or {},
        "request": request,
    }
    if json_data is not None:
        kwargs["json"] = json_data
    elif text:
        kwargs["text"] = text
    else:
        kwargs["text"] = ""
    return httpx.Response(**kwargs)


def test_get_video_gen_provider_agnes() -> None:
    cls = get_video_gen_provider("agnes")
    assert cls is AgnesVideoGenerationClient


@pytest.mark.asyncio
async def test_create_task_text_to_video() -> None:
    """Text-to-video: request body contains model + prompt only."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "id": "task_123",
                "task_id": "task_123",
                "video_id": "video_456",
                "object": "video",
                "model": "agnes-video-v2.0",
                "status": "queued",
                "progress": 0,
                "created_at": 1780457477,
                "seconds": "10.0",
                "size": "1280x768",
            })
        )),
    )

    response = await client.create_task(
        model="agnes-video-v2.0",
        prompt="A cat walking on the beach at sunset",
    )

    assert response.video_id == "video_456"
    assert response.task_id == "task_123"
    assert response.status == "queued"
    assert response.progress == 0
    assert response.seconds == "10.0"
    assert response.size == "1280x768"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_create_task_with_image() -> None:
    """Image-to-video: request body includes image field."""
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        captured_body.update(json.loads(req.content.decode()))
        return _mock_response(200, {
            "video_id": "video_789",
            "task_id": "task_789",
            "status": "queued",
            "progress": 0,
        })

    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await client.create_task(
        model="agnes-video-v2.0",
        prompt="Make this image move",
        image="https://example.com/photo.jpg",
        width=1152,
        height=768,
        num_frames=121,
        frame_rate=24,
    )

    assert captured_body["model"] == "agnes-video-v2.0"
    assert captured_body["image"] == "https://example.com/photo.jpg"
    assert captured_body["width"] == 1152
    assert captured_body["num_frames"] == 121
    await client._client.aclose()


@pytest.mark.asyncio
async def test_create_task_429_retryable() -> None:
    """429 with rate-limit message should retry and succeed."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(
                429,
                text='{"error": {"message": "rate_limit_exceeded"}}',
                headers={"retry-after": "0.1"},
                request=req,
            )
        return _mock_response(200, {
            "video_id": "video_retry",
            "task_id": "task_retry",
            "status": "queued",
            "progress": 0,
        })

    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await client.create_task(model="agnes-video-v2.0", prompt="test")
    assert call_count == 3
    assert response.video_id == "video_retry"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_create_task_429_quota_exceeded_no_retry() -> None:
    """429 with quota-exceeded message should NOT retry."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            429,
            text='{"error": {"message": "insufficient_quota"}}',
            request=req,
        )

    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(VideoGenerationError, match="quota exceeded"):
        await client.create_task(model="agnes-video-v2.0", prompt="test")
    assert call_count == 1
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_completed() -> None:
    """Querying a completed task returns video_url."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "id": "task_123",
                "video_id": "task_123",
                "task_id": "task_123",
                "object": "video",
                "model": "agnes-video-v2.0",
                "status": "completed",
                "progress": 100,
                "created_at": 1784530473,
                "completed_at": 1784530510,
                "seconds": "1.0",
                "size": "832x448",
                "metadata": {
                    "url": "https://platform-outputs.agnes-ai.space/videos/test.mp4",
                    "size_mapping": {
                        "adjusted": True,
                        "resolution": "480p",
                        "ratio": "16:9",
                    },
                },
            })
        )),
    )

    status = await client.get_task_status("task_123")
    assert status.status == "completed"
    assert status.progress == 100
    assert status.video_url == "https://platform-outputs.agnes-ai.space/videos/test.mp4"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_in_progress() -> None:
    """Querying an in-progress task."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "video_456",
                "task_id": "task_456",
                "status": "in_progress",
                "progress": 45,
            })
        )),
    )

    status = await client.get_task_status("video_456")
    assert status.status == "in_progress"
    assert status.progress == 45
    assert status.video_url is None
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_failed() -> None:
    """Querying a failed task returns error."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "video_fail",
                "task_id": "task_fail",
                "status": "failed",
                "progress": 50,
                "error": {"message": "content policy violation"},
            })
        )),
    )

    status = await client.get_task_status("video_fail")
    assert status.status == "failed"
    assert status.error == "content policy violation"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_create_task_missing_api_key() -> None:
    """Missing API key raises VideoGenerationError."""
    client = AgnesVideoGenerationClient(api_key="")
    with pytest.raises(VideoGenerationError, match="not configured"):
        await client.create_task(model="agnes-video-v2.0", prompt="test")


@pytest.mark.asyncio
async def test_retry_after_header_seconds() -> None:
    """Retry-After header in seconds format is parsed."""
    response = httpx.Response(
        429,
        headers={"retry-after": "5"},
        request=httpx.Request("POST", "https://example.com"),
    )
    assert AgnesVideoGenerationClient._extract_retry_after(response) == 5.0


@pytest.mark.asyncio
async def test_is_retryable_429_rate_limit() -> None:
    """Rate limit 429 is retryable."""
    response = httpx.Response(
        429,
        text='{"error": {"message": "rate_limit_exceeded"}}',
        request=httpx.Request("POST", "https://example.com"),
    )
    assert AgnesVideoGenerationClient._is_retryable_429(response) is True


@pytest.mark.asyncio
async def test_is_retryable_429_quota_not_retryable() -> None:
    """Quota exceeded 429 is NOT retryable."""
    response = httpx.Response(
        429,
        text='{"error": {"message": "insufficient_quota"}}',
        request=httpx.Request("POST", "https://example.com"),
    )
    assert AgnesVideoGenerationClient._is_retryable_429(response) is False


@pytest.mark.asyncio
async def test_is_retryable_429_non_429_returns_false() -> None:
    """Non-429 status returns False."""
    response = httpx.Response(
        500,
        text="server error",
        request=httpx.Request("POST", "https://example.com"),
    )
    assert AgnesVideoGenerationClient._is_retryable_429(response) is False


# -- Status normalization tests ---------------------------------------------


def test_normalize_status_succeed() -> None:
    """'succeed' should be normalized to 'completed'."""
    assert _normalize_status("succeed") == "completed"


def test_normalize_status_success() -> None:
    """'success' should be normalized to 'completed'."""
    assert _normalize_status("success") == "completed"


def test_normalize_status_done() -> None:
    """'done' should be normalized to 'completed'."""
    assert _normalize_status("done") == "completed"


def test_normalize_status_finished() -> None:
    """'finished' should be normalized to 'completed'."""
    assert _normalize_status("finished") == "completed"


def test_normalize_status_completed_unchanged() -> None:
    """'completed' stays 'completed'."""
    assert _normalize_status("completed") == "completed"


def test_normalize_status_in_progress_unchanged() -> None:
    """'in_progress' stays 'in_progress'."""
    assert _normalize_status("in_progress") == "in_progress"


def test_normalize_status_case_insensitive() -> None:
    """Normalization is case-insensitive."""
    assert _normalize_status("SUCCEED") == "completed"
    assert _normalize_status("Success") == "completed"


# -- URL extraction tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_status_url_in_metadata() -> None:
    """Video URL extracted from metadata.url (standard location)."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "vid_1",
                "status": "completed",
                "progress": 100,
                "metadata": {"url": "https://cdn.example.com/v1.mp4"},
            })
        )),
    )
    status = await client.get_task_status("vid_1")
    assert status.video_url == "https://cdn.example.com/v1.mp4"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_url_top_level() -> None:
    """Video URL extracted from top-level 'url' field."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "vid_2",
                "status": "succeed",
                "progress": 100,
                "url": "https://cdn.example.com/v2.mp4",
            })
        )),
    )
    status = await client.get_task_status("vid_2")
    assert status.status == "completed"  # normalized
    assert status.video_url == "https://cdn.example.com/v2.mp4"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_url_video_url_field() -> None:
    """Video URL extracted from top-level 'video_url' field."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "vid_3",
                "status": "done",
                "progress": 100,
                "video_url": "https://cdn.example.com/v3.mp4",
            })
        )),
    )
    status = await client.get_task_status("vid_3")
    assert status.status == "completed"  # normalized
    assert status.video_url == "https://cdn.example.com/v3.mp4"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_url_in_result_object() -> None:
    """Video URL extracted from nested result.url field."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "vid_4",
                "status": "success",
                "progress": 100,
                "result": {"url": "https://cdn.example.com/v4.mp4"},
            })
        )),
    )
    status = await client.get_task_status("vid_4")
    assert status.status == "completed"  # normalized
    assert status.video_url == "https://cdn.example.com/v4.mp4"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_no_url() -> None:
    """No URL anywhere returns None."""
    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: _mock_response(200, {
                "video_id": "vid_5",
                "status": "in_progress",
                "progress": 80,
            })
        )),
    )
    status = await client.get_task_status("vid_5")
    assert status.video_url is None
    await client._client.aclose()


# -- get_task_status 429 retry tests -----------------------------------------


@pytest.mark.asyncio
async def test_get_task_status_429_retryable() -> None:
    """get_task_status retries on retryable 429 and succeeds."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return httpx.Response(
                429,
                text='{"error": {"message": "rate_limit_exceeded"}}',
                headers={"retry-after": "0.1"},
                request=req,
            )
        return _mock_response(200, {
            "video_id": "vid_retry",
            "status": "completed",
            "progress": 100,
            "metadata": {"url": "https://cdn.example.com/v.mp4"},
        })

    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    status = await client.get_task_status("vid_retry")
    assert call_count == 2
    assert status.status == "completed"
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_task_status_429_quota_no_retry() -> None:
    """get_task_status does NOT retry on quota-exceeded 429."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            429,
            text='{"error": {"message": "insufficient_quota"}}',
            request=req,
        )

    client = AgnesVideoGenerationClient(
        api_key="test-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(VideoGenerationError, match="quota exceeded"):
        await client.get_task_status("vid_quota")
    assert call_count == 1
    await client._client.aclose()
