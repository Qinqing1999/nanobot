"""Tests for the apply_mask tool."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

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
async def test_size_mismatch(tmp_path):
    ip = tmp_path / "i.png"
    ip.write_bytes(_img(4, 4))
    mp = tmp_path / "m.png"
    mp.write_bytes(_mask(8, 8))
    result = await _tool(str(tmp_path)).execute(image=str(ip), mask=str(mp))
    assert isinstance(result, ToolResult) and result.is_error
    assert "不一致" in str(result)


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
