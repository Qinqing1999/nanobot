"""Tests for the apply_mask tool."""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.apply_mask import ApplyMaskTool
from nanobot.agent.tools.base import ToolResult


def _img(w=4, h=4, c=(255, 0, 0)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), c).save(b, format="PNG")
    return b.getvalue()


def _mask(w=4, h=4):
    from PIL import Image
    m = Image.new("L", (w, h), 0)
    for x in range(w // 2):
        for y in range(h):
            m.putpixel((x, y), 255)
    b = io.BytesIO()
    m.save(b, format="PNG")
    return b.getvalue()


def _tool(ws="/tmp", sess=None, bus=None):
    return ApplyMaskTool(workspace=ws, sessions=sess, bus=bus or MagicMock())


def test_always_enabled():
    from nanobot.agent.tools.context import ToolContext
    assert ApplyMaskTool.enabled(ToolContext(config=MagicMock(), workspace="/tmp"))


@pytest.mark.asyncio
async def test_white_bg(tmp_path):
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4, (255, 0, 0)))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(4, 4))
    r = json.loads(await _tool(str(tmp_path)).execute(image=str(ip), mask=str(mp), background="white"))
    from PIL import Image
    ri = Image.open(r["artifacts"][0]["path"])
    assert ri.getpixel((0, 0))[:3] == (255, 0, 0)
    assert ri.getpixel((3, 0))[:3] == (255, 255, 255)


@pytest.mark.asyncio
async def test_transparent_bg(tmp_path):
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4, (0, 255, 0)))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(4, 4))
    r = json.loads(await _tool(str(tmp_path)).execute(image=str(ip), mask=str(mp), background="transparent"))
    from PIL import Image
    ri = Image.open(r["artifacts"][0]["path"])
    assert ri.mode == "RGBA"
    assert ri.getpixel((0, 0))[3] == 255
    assert ri.getpixel((3, 0))[3] == 0


@pytest.mark.asyncio
async def test_default_bg_white(tmp_path):
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4, (100, 50, 25)))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(4, 4))
    r = json.loads(await _tool(str(tmp_path)).execute(image=str(ip), mask=str(mp)))
    from PIL import Image
    ri = Image.open(r["artifacts"][0]["path"])
    assert ri.getpixel((3, 0))[:3] == (255, 255, 255)


@pytest.mark.asyncio
async def test_size_mismatch_auto_resize(tmp_path):
    """When mask dimensions differ from image, apply_mask auto-resizes the mask
    and produces a valid result instead of erroring."""
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(8, 8))
    r = json.loads(await _tool(str(tmp_path)).execute(image=str(ip), mask=str(mp)))
    from PIL import Image
    ri = Image.open(r["artifacts"][0]["path"])
    assert ri.size == (4, 4)  # matches image, not mask


@pytest.mark.asyncio
async def test_mask_not_found(tmp_path):
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4))
    result = await _tool(str(tmp_path)).execute(image=str(ip), mask="/nonexistent/m.png")
    assert isinstance(result, ToolResult) and result.is_error


@pytest.mark.asyncio
async def test_unsupported_format(tmp_path):
    bp = tmp_path / "t.txt"
    bp.write_text("hello")
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(4, 4))
    result = await _tool(str(tmp_path)).execute(image=str(bp), mask=str(mp))
    assert isinstance(result, ToolResult) and result.is_error
    assert "unsupported" in str(result)


# ---------------------------------------------------------------------------
# Auto-delivery tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_deliver_image_with_notification(tmp_path):
    """apply_mask sends OutboundMessage with both media (image file) and
    content (artifact ID notification) to the user via MessageBus."""
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.bus.events import OutboundMessage
    from nanobot.session.manager import SessionManager

    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4, (255, 0, 0)))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(4, 4))

    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    sessions = SessionManager(tmp_path)
    tool = _tool(str(tmp_path), sess=sessions, bus=bus)

    with request_context(RequestContext(
        channel="weixin",
        chat_id="test-user",
        session_key="weixin:test-user",
    )):
        result_str = await tool.execute(image=str(ip), mask=str(mp))

    # Verify the tool returned a valid result
    result = json.loads(result_str)
    assert "artifacts" in result
    artifact_path = result["artifacts"][0]["path"]

    # Verify bus.publish_outbound was called with media containing the image path
    bus.publish_outbound.assert_awaited_once()
    outgoing: OutboundMessage = bus.publish_outbound.await_args.args[0]
    assert outgoing.channel == "weixin"
    assert outgoing.chat_id == "test-user"
    assert artifact_path in outgoing.media
    assert "制品 ID" in outgoing.content
