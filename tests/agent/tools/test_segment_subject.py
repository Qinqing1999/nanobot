"""Tests for the segment_subject tool — BiRefNet HTTP call, mask saving, and error handling."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.segment_subject import SegmentSubjectTool
from nanobot.config.schema import ProviderConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png(width: int = 4, height: int = 4, color: tuple[int, ...] = (255, 0, 0)) -> bytes:
    """Create a small PNG image in memory."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_mask_png(width: int = 4, height: int = 4) -> bytes:
    """Create a small black-and-white mask PNG: left half white, right half black."""
    from PIL import Image
    img = Image.new("L", (width, height), 0)  # all black
    for x in range(width // 2):
        for y in range(height):
            img.putpixel((x, y), 255)  # white
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_data_url(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _make_tool(
    *,
    api_base: str = "http://localhost:8001",
    workspace: str = "/tmp",
    sessions: Any = None,
) -> SegmentSubjectTool:
    return SegmentSubjectTool(
        workspace=workspace,
        api_base=api_base,
        sessions=sessions,
    )


# ---------------------------------------------------------------------------
# enabled() tests
# ---------------------------------------------------------------------------

def test_enabled_when_birefnet_configured() -> None:
    ctx = ToolContext(
        config=MagicMock(),
        workspace="/tmp",
        provider_configs={"birefnet": ProviderConfig(api_base="http://localhost:8001")},
    )
    assert SegmentSubjectTool.enabled(ctx) is True


def test_disabled_when_birefnet_not_configured() -> None:
    ctx = ToolContext(
        config=MagicMock(),
        workspace="/tmp",
        provider_configs=None,
    )
    assert SegmentSubjectTool.enabled(ctx) is False


def test_disabled_when_birefnet_api_base_empty() -> None:
    ctx = ToolContext(
        config=MagicMock(),
        workspace="/tmp",
        provider_configs={"birefnet": ProviderConfig()},
    )
    assert SegmentSubjectTool.enabled(ctx) is False


# ---------------------------------------------------------------------------
# execute() tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_returns_mask_path(tmp_path: Path) -> None:
    """Tool calls BiRefNet, saves mask, returns mask_path in JSON."""
    image_bytes = _make_png(4, 4)
    image_path = tmp_path / "input.png"
    image_path.write_bytes(image_bytes)

    mask_bytes = _make_mask_png(4, 4)
    mask_data_url = _make_data_url(mask_bytes)

    tool = _make_tool(workspace=str(tmp_path))

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"mask": mask_data_url}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result_str = await tool.execute(image=str(image_path))

    result = json.loads(result_str)
    assert "mask_path" in result
    assert Path(result["mask_path"]).is_file()
    assert result["service"] == "birefnet"
    assert "next_step" in result


@pytest.mark.asyncio
async def test_execute_error_service_unavailable(tmp_path: Path) -> None:
    """Tool returns error when BiRefNet service is unreachable."""
    image_bytes = _make_png(4, 4)
    image_path = tmp_path / "input.png"
    image_path.write_bytes(image_bytes)

    tool = _make_tool(workspace=str(tmp_path))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(image=str(image_path))

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "无法连接分割服务" in str(result)


@pytest.mark.asyncio
async def test_execute_error_timeout(tmp_path: Path) -> None:
    """Tool returns error when BiRefNet service times out."""
    image_bytes = _make_png(4, 4)
    image_path = tmp_path / "input.png"
    image_path.write_bytes(image_bytes)

    tool = _make_tool(workspace=str(tmp_path))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(image=str(image_path))

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "超时" in str(result)


@pytest.mark.asyncio
async def test_execute_error_no_mask_in_response(tmp_path: Path) -> None:
    """Tool returns error when service response lacks mask field."""
    image_bytes = _make_png(4, 4)
    image_path = tmp_path / "input.png"
    image_path.write_bytes(image_bytes)

    tool = _make_tool(workspace=str(tmp_path))

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": "something went wrong"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await tool.execute(image=str(image_path))

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "未返回有效蒙版" in str(result)


@pytest.mark.asyncio
async def test_execute_error_unsupported_format(tmp_path: Path) -> None:
    """Tool returns error for non-image files."""
    bad_path = tmp_path / "not_image.txt"
    bad_path.write_text("this is not an image")

    tool = _make_tool(workspace=str(tmp_path))
    result = await tool.execute(image=str(bad_path))

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "unsupported image format" in str(result)


@pytest.mark.asyncio
async def test_execute_error_nonexistent_file(tmp_path: Path) -> None:
    """Tool returns error for non-existent file."""
    tool = _make_tool(workspace=str(tmp_path))
    result = await tool.execute(image=str(tmp_path / "nonexistent.png"))

    assert isinstance(result, ToolResult)
    assert result.is_error


# ---------------------------------------------------------------------------
# check_service_health() tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_service_health_ok() -> None:
    tool = _make_tool()

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        healthy = await tool.check_service_health()

    assert healthy is True


@pytest.mark.asyncio
async def test_check_service_health_unreachable() -> None:
    tool = _make_tool()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        healthy = await tool.check_service_health()

    assert healthy is False
