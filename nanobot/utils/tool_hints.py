"""Tool hint formatting for concise, human-readable tool call display.

Hints shown to users are **generic descriptions** (e.g. "执行命令", "读取文件")
without raw arguments, paths, or command text.  This keeps the progress UI
clean and avoids leaking diagnostic commands to end users.
"""

from __future__ import annotations

from nanobot.providers.base import ToolCallRequest

# Registry: tool_name -> user-facing label (no arguments exposed).
_TOOL_LABELS: dict[str, str] = {
    "read_file":           "读取文件",
    "write_file":          "写入文件",
    "edit":                "编辑文件",
    "edit_file":           "编辑文件",
    "apply_patch":         "应用补丁",
    "find_files":          "查找文件",
    "grep":                "搜索内容",
    "exec":                "执行命令",
    "list_exec_sessions":  "执行会话",
    "poll_exec_session":   "轮询会话",
    "write_stdin":         "写入输入",
    "web_search":          "搜索网络",
    "x_search":            "搜索 X",
    "web_fetch":           "获取网页",
    "list_dir":            "列出目录",
    "generate_image":      "生成图片",
    "generate_video":      "生成视频",
    "check_video":         "查询视频",
    "message":             "发送消息",
    "create_goal":         "创建目标",
    "update_goal":         "更新目标",
    "my":                  "检查状态",
    "spawn_subagent":      "启动子代理",
}


def format_tool_hints(tool_calls: list[ToolCallRequest], max_length: int = 40) -> str:
    """Format tool calls as concise, user-facing labels without raw arguments."""
    if not tool_calls:
        return ""

    formatted: list[str] = []
    for tc in tool_calls:
        name = getattr(tc, "name", None)
        if not isinstance(name, str) or not name:
            continue
        label = _TOOL_LABELS.get(name)
        if label:
            formatted.append(label)
        elif name.startswith("mcp_"):
            formatted.append(_fmt_mcp(name))
        else:
            formatted.append(name)

    hints: list[tuple[str, int]] = []
    for hint in formatted:
        if hints and hints[-1][0] == hint:
            hints[-1] = (hint, hints[-1][1] + 1)
        else:
            hints.append((hint, 1))

    return ", ".join(
        f"{h} \u00d7 {c}" if c > 1 else h for h, c in hints
    )


def _fmt_mcp(name: str) -> str:
    """Format MCP tool as server::tool (no arguments)."""
    if "__" in name:
        parts = name.split("__", 1)
        server = parts[0].removeprefix("mcp_")
        tool = parts[1]
    else:
        rest = name.removeprefix("mcp_")
        parts = rest.split("_", 1)
        server = parts[0] if parts else rest
        tool = parts[1] if len(parts) > 1 else ""
    if not tool:
        return name
    return f"{server}::{tool}"
