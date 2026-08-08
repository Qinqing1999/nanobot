# 会话管理命令：/sessions 和 /switch

## 状态
已接受

## 背景
nanobot 已有 `/new` 命令（重置当前会话），但用户无法查看当前 channel 的所有会话列表，也无法在会话之间切换。特别是微信等渠道中，用户不知道如何开始新对话或回到之前的对话。

## 决策

新增两个 slash 命令，利用已有的 `session_key_override` 机制实现会话切换：

1. **`/sessions`**：列出当前 channel:chat_id 下的所有会话（从 SessionManager 的 session 列表中筛选）。
2. **`/switch [id]`**：切换到指定会话。通过设置 `InboundMessage.session_key_override` 实现。
3. **改造 `/new`**：不再清空当前会话，而是创建一个新会话（新 session key），保留旧会话。
4. **会话 Key 编码**：在 `{channel}:{chat_id}` 后追加 `:{n}` 后缀（如 `wechat:wx_user:2`）。

## 考虑过的替代方案

- **只做 `/new` 不做切换**：用户无法回到之前的对话。
- **通过 WebUI 实现**：微信等非 WebUI 渠道的用户无法使用。

## 后果

- Session key 格式从 `{channel}:{chat_id}` 变为 `{channel}:{chat_id}[:{n}]`，需要向后兼容。
- Channel 需要维护"当前会话序号"的状态。
- `SessionManager.list_sessions()` 需要支持按 channel:chat_id 前缀过滤。
