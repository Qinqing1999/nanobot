# ADR 0013: apply_mask 自动发送图片给用户

## Status

Accepted

## Context

`apply_mask` 工具在完成主体提取后需要把结果图片交付给用户。有两种方案：

- **方案 A（自动发送）**：`apply_mask` 在 `_push_artifact_notification` 中直接把图片文件路径放入 `OutboundMessage.media`，由频道层（如微信）自动上传并发送给用户。
- **方案 B（agent 驱动发送）**：`apply_mask` 只注册制品 + 发文字通知，在 skill 中写明"agent 必须接着调 `message(media=[path])` 发图"。

当前实现走的是方案 B 的前半段（注册 + 文字通知），但缺少 skill 指令告诉 agent 调 `message`，导致图片到不了用户手里。

## Decision

采用方案 A：`apply_mask` 自动发送图片。

`_push_artifact_notification` 在 `OutboundMessage` 中同时包含：
1. `content`：制品 ID 文字通知（"📎 已注册制品: ID 1001..."）
2. `media`：图片文件路径列表

频道层（微信、Telegram 等）遍历 `msg.media` 调用 `_send_media_file` 上传并发送图片。

## Consequences

- 用户在 `apply_mask` 完成后直接收到图片，无需 agent 额外调用 `message` 工具。
- `generate_image` 仍然保持 agent 驱动发送（通过 `message` 工具），因为 `generate_image` 可能在一轮中生成多张图片，agent 需要决定发送时机和内容。
- 如果 `current_request_context()` 为 None（上下文丢失），图片和通知都无法发送。这是已有的限制，不是本次引入的。
- `subject-extraction` skill 中明确写明"不需要额外调用 message 工具"，避免 agent 重复发送。
