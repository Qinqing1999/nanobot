# 视频生成：后台轮询 + 主动推送模式

## 状态
已接受

## 背景
Agnes Video V2.0 API 是异步的：创建任务返回 `video_id`，需要轮询直到完成。视频生成可能需要数十秒到数分钟，阻塞式轮询（模式 A）会导致 agent 无法处理其他消息。

## 决策

采用模式 B — 后台轮询 + 主动推送：

1. **两工具架构**：`create_video` 创建任务并启动后台轮询；`check_video` 按需查询任务状态。
2. **后台轮询**：通过 `AgentLoop.schedule_background()` 启动 asyncio 后台任务，独立于 agent 主循环。
3. **主动推送**：视频生成完成后，后台任务通过 `MessageBus.publish_outbound()` 发送 `OutboundMessage`（含本地 MP4 文件路径）。
4. **重启策略**：接受"重启 = 丢失"。后台任务是纯内存 asyncio task，不持久化。
5. **超时**：5 分钟上限，超时后取消任务并推送超时消息。
6. **视频交付**：下载 MP4 到本地 media 目录，存储为 artifact，通过 `OutboundMessage.media` 发送本地文件路径。
7. **进度提示**：开始生成时推送一条消息，进度达 50% 时推送一条，完成时推送最终结果。中间不推送。
8. **429 重试**：复用 nanobot 现有的 429 分类机制（`_RETRYABLE_429_TEXT_MARKERS` vs `_NON_RETRYABLE_429_TEXT_MARKERS`）。创建任务最多重试 3 次，遵循 `Retry-After` 头。

## 考虑过的替代方案

- **模式 A（阻塞式轮询）**：实现简单，但 agent 在等待期间无法处理其他消息，用户体验差。
- **模式 B 变体 — 持久化 task_id**：重启后可恢复轮询。增加复杂度但当前需求不支持。

## 后果

- 需要一个 per-session 的视频任务跟踪表（内存中），记录 `video_id → session_key/channel/chat_id` 映射。
- `check_video` 工具允许 agent 主动查询任务状态（如用户问"视频好了吗？"）。
- 后台任务完成后需要下载视频文件、注册 artifact、发送消息——这一链条较长，需要良好的错误处理。
