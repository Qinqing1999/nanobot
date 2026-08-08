"""Tests for tool hint formatting (nanobot.utils.tool_hints).

Hints are now **generic labels** (e.g. "执行命令", "读取文件") without
raw arguments, paths, or command text.
"""

from nanobot.providers.base import ToolCallRequest
from nanobot.utils.tool_hints import format_tool_hints


def _tc(name: str, args) -> ToolCallRequest:
    return ToolCallRequest(id="c1", name=name, arguments=args)


def _hint(calls, max_length=40):
    """Shortcut for format_tool_hints."""
    return format_tool_hints(calls, max_length=max_length)


class TestToolHintKnownTools:
    """Registered tools produce user-facing labels without arguments."""

    def test_read_file(self):
        result = _hint([_tc("read_file", {"path": "foo.txt"})])
        assert result == "读取文件"

    def test_write_file(self):
        result = _hint([_tc("write_file", {"path": "docs/api.md", "content": "..."})])
        assert result == "写入文件"

    def test_edit(self):
        result = _hint([_tc("edit", {"file_path": "src/main.py", "old_string": "x"})])
        assert result == "编辑文件"

    def test_edit_file_alias(self):
        result = _hint([_tc("edit_file", {"path": "src/main.py"})])
        assert result == "编辑文件"

    def test_apply_patch(self):
        result = _hint([_tc("apply_patch", {"path": "src/main.py"})])
        assert result == "应用补丁"

    def test_grep(self):
        result = _hint([_tc("grep", {"pattern": "TODO|FIXME", "path": "src"})])
        assert result == "搜索内容"

    def test_exec(self):
        result = _hint([_tc("exec", {"command": "npm install typescript"})])
        assert result == "执行命令"

    def test_exec_long_command_not_exposed(self):
        cmd = "cd /very/long/path && cat file && echo done && sleep 1 && ls -la"
        result = _hint([_tc("exec", {"command": cmd})])
        assert result == "执行命令"
        assert "cd" not in result
        assert "npm" not in result

    def test_web_search(self):
        result = _hint([_tc("web_search", {"query": "Claude 4 vs GPT-4"})])
        assert result == "搜索网络"

    def test_web_fetch(self):
        result = _hint([_tc("web_fetch", {"url": "https://example.com/page"})])
        assert result == "获取网页"

    def test_list_dir(self):
        result = _hint([_tc("list_dir", {"path": "/tmp"})])
        assert result == "列出目录"

    def test_generate_image(self):
        result = _hint([_tc("generate_image", {"prompt": "a cat"})])
        assert result == "生成图片"

    def test_generate_video(self):
        result = _hint([_tc("generate_video", {"prompt": "a sunset"})])
        assert result == "生成视频"

    def test_check_video(self):
        result = _hint([_tc("check_video", {"video_id": "vid_123"})])
        assert result == "查询视频"

    def test_message(self):
        result = _hint([_tc("message", {"content": "hello"})])
        assert result == "发送消息"


class TestToolHintMCP:
    """MCP tools are abbreviated to server::tool format (no arguments)."""

    def test_mcp_standard_format(self):
        result = _hint([_tc("mcp_4_5v_mcp__analyze_image", {"imageSource": "https://img.jpg"})])
        assert "4_5v" in result
        assert "analyze_image" in result
        # No arguments should be shown
        assert "img.jpg" not in result

    def test_mcp_simple_name(self):
        result = _hint([_tc("mcp_github__create_issue", {"title": "Bug fix"})])
        assert "github" in result
        assert "create_issue" in result
        assert "Bug fix" not in result


class TestToolHintFallback:
    """Unknown tools fall back to just the tool name (no arguments)."""

    def test_unknown_tool_with_string_arg(self):
        result = _hint([_tc("custom_tool", {"data": "hello world"})])
        assert result == "custom_tool"

    def test_unknown_tool_no_string_arg(self):
        result = _hint([_tc("custom_tool", {"count": 42})])
        assert result == "custom_tool"

    def test_empty_tool_calls(self):
        result = _hint([])
        assert result == ""


class TestToolHintFolding:
    """Consecutive same-label calls are folded with ×N."""

    def test_single_call_no_fold(self):
        calls = [_tc("grep", {"pattern": "*.py"})]
        result = _hint(calls)
        assert "\u00d7" not in result

    def test_two_consecutive_same_tool_folded(self):
        """Same tool label → folded even with different args."""
        calls = [
            _tc("grep", {"pattern": "*.py"}),
            _tc("grep", {"pattern": "*.ts"}),
        ]
        result = _hint(calls)
        assert "\u00d7 2" in result

    def test_different_tools_not_folded(self):
        calls = [
            _tc("grep", {"pattern": "TODO"}),
            _tc("read_file", {"path": "a.py"}),
        ]
        result = _hint(calls)
        assert "\u00d7" not in result

    def test_interleaved_same_tools_not_folded(self):
        calls = [
            _tc("grep", {"pattern": "a"}),
            _tc("read_file", {"path": "f.py"}),
            _tc("grep", {"pattern": "b"}),
        ]
        result = _hint(calls)
        assert "\u00d7" not in result


class TestToolHintMultipleCalls:
    """Multiple different tool calls are comma-separated."""

    def test_two_different_tools(self):
        calls = [
            _tc("grep", {"pattern": "TODO"}),
            _tc("read_file", {"path": "main.py"}),
        ]
        result = _hint(calls)
        assert "搜索内容" in result
        assert "读取文件" in result
        assert ", " in result


class TestToolHintEdgeCases:
    """Edge cases and defensive handling."""

    def test_known_tool_empty_list_args(self):
        result = _hint([_tc("read_file", [])])
        assert result == "读取文件"

    def test_known_tool_none_args(self):
        result = _hint([_tc("read_file", None)])
        assert result == "读取文件"

    def test_fallback_empty_list_args(self):
        result = _hint([_tc("custom_tool", [])])
        assert result == "custom_tool"

    def test_fallback_none_args(self):
        result = _hint([_tc("custom_tool", None)])
        assert result == "custom_tool"


class TestToolHintMalformedCalls:
    """Malformed tool calls must not crash hint formatting."""

    def test_none_name_is_skipped(self):
        result = _hint([_tc(None, None)])
        assert result == ""

    def test_empty_name_is_skipped(self):
        result = _hint([_tc("", {"path": "foo.txt"})])
        assert result == ""

    def test_none_name_mixed_with_valid_call(self):
        result = _hint([_tc(None, None), _tc("read_file", {"path": "foo.txt"})])
        assert result == "读取文件"
