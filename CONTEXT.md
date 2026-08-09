# nanobot 媒体生成与引用上下文

nanobot 的媒体（图片、视频、文件）生成、存储、引用和交付系统涉及的领域概念和统一术语。

## 语言

**制品 (Artifact)**:
用户上传或 agent 生成的任何媒体文件（图片、视频、PPT、Word 等），注册后获得唯一数字 ID，可在整个 session 内被引用。
_Avoid_: 文件（File，太泛）、媒体（Media，指传输层）、资产（Asset，太财务化）

**制品注册表 (Artifact Registry)**:
Session 级别的制品 ID 到本地文件路径的映射存储。每个制品注册时获得四位数字 ID，按类型前缀编号。
_Avoid_: 文件管理器、媒体库

**制品 ID (Artifact ID)**:
四位纯数字标识符，首位为类型前缀：0=文档文件（PPT/Word/PDF 等），1=图片，2=视频，3=音频。Session 内唯一，跨 session 不保证唯一。
_Avoid_: 文件名、路径、UUID、哈希

**会话 (Session)**:
一段连续对话的上下文容器，由 `session_key`（`{channel}:{chat_id}` 或带后缀）唯一标识。制品注册表的生命周期绑定于会话。
_Avoid_: 对话（Conversation，指内容）、线程（Thread，指 UI 概念）

**视频任务 (Video Task)**:
通过异步 API 创建的视频生成任务。创建后返回 `video_id`，后台轮询任务定期查询状态，完成后自动下载视频文件并推送给用户。
_Avoid_: 视频请求、渲染任务

**后台轮询任务 (Background Polling Task)**:
通过 `AgentLoop.schedule_background()` 启动的 asyncio 协程，独立于 agent 主循环运行。负责定期查询视频任务状态并在完成时主动推送结果。
_Avoid_: 定时器、cron 任务

**主动推送 (Proactive Push)**:
后台任务完成后，通过 `MessageBus.publish_outbound()` 向用户所在频道发送 `OutboundMessage`（含视频文件附件）的机制。不需要用户或 agent 主动查询。
_Avoid_: 回调、webhook

**制品通知 (Artifact Notification)**:
当文件上传或生成后，通道层或工具层通过 `OutboundMessage` 向用户发送的一条系统消息，包含制品 ID、类型、文件名。采用 Markdown 格式（`**KEY**: `VALUE``）。与 LLM 回复独立，用户在 LLM 处理完成前即可看到 ID。
_Avoid_: 制品事件（Artifact Event，指 WebSocket 专用事件）、制品回执

**进度提示 (Progress Hint)**:
在工具执行期间或后台任务运行期间，向用户频道推送的状态提示消息。视频生成采用"开始 → 50% → 完成"三阶段推送。工具提示文本标签（如"执行命令"、"读取文件"）不再向用户展示；仅保留结构化工具事件（tool_events）供 WebUI 渲染富活动卡片（文件编辑 diff、web 搜索结果等）。由 `send_tool_hints` 配置标志控制。
_Avoid_: 日志、调试输出

**制品引用解析 (Artifact Reference Resolution)**:
消息预处理阶段，从用户文本中提取四位数字候选并查询制品注册表，命中时将对应文件路径加入 `InboundMessage.media` 并将文本中的 ID 替换为描述性引用。发生在 `_restore_turn` 中，LLM 不可见。
_Avoid_: ID 查找、文件定位

**会话切换 (Session Switching)**:
用户通过 `/sessions` 和 `/switch` 命令在同一 channel 内查看、新建和切换不同会话的能力。通过 `session_key_override` 实现。
_Avoid_: 多租户、频道切换

## 限速与配额

**限速 (Rate Limit)**:
API 服务端因短时间内请求频率过高而返回 HTTP 429，属于临时性拒绝。可通过等待后重试解决，通常附带 `Retry-After` 响应头指示等待时间。与配额耗尽互斥。
_Avoid_: 频率限制、节流（Throttle，指传输层概念）

**配额耗尽 (Quota Exhaustion)**:
API 服务端因账户配额、余额或信用额度用尽而返回 HTTP 429，属于持久性拒绝。重试无意义，需要充值或等待配额重置。通过响应体中的特定标记（如 `insufficient_quota`、`billing_hard_limit_reached`）与限速区分。
_Avoid_: 额度用完、额度不足

**创建轮询 (Creation Polling)**:
视频任务创建遇到限速时，不立即放弃，而是以固定间隔（15 秒）持续重试创建请求，直到成功或超过最大等待时间（3 分钟）。与后台轮询任务不同——创建轮询发生在任务创建阶段，后台轮询发生在任务创建成功后的状态查询阶段。创建轮询在后台 asyncio 任务中非阻塞运行，agent 主循环不受影响。
_Avoid_: 创建重试、任务排队

**待处理视频任务 (Pending Video Task)**:
从首次创建尝试到终端状态（交付/失败/超时）期间的视频任务记录，用于防止同一 session 内参数完全相同的重复提交。存储在模块级内存 dict 中，键为 session_key 和任务参数的哈希。重启丢失（与后台轮询任务一致）。
_Avoid_: 视频队列、任务缓存

## 视频生成模式

**生成模式 (Generation Mode)**:
视频生成时图片输入的使用方式，由 `mode` 字段标识。四种模式：`ti2vid`（纯文生视频，无图片）、`img2vid`（单图生视频）、`multi_reference`（多图参考，2-4 张图片作为风格/内容参考，无时间顺序）、`keyframes`（关键帧插值，恰好 2 张图片定义首帧和尾帧，生成过渡动画）。`mode` 可由图片参数自动推断，无需用户显式指定。
_Avoid_: 渲染模式、输出类型

**参考图 (Reference Image)**:
视频生成时作为风格或内容参考的图片，无时间顺序语义。通过 `reference_images` 参数传入（artifact ID 数组）。1 张时为单图生视频，2-4 张时为多图参考。与关键帧互斥。
_Avoid_: 源图、输入图

**关键帧 (Keyframe)**:
视频生成时定义时间端点的图片，恰好 2 张：首帧（起点）和尾帧（终点）。通过 `keyframe_images` 参数传入。API 在两帧之间生成过渡动画。与参考图互斥。
_Avoid_: 参考帧、定格帧

**参考图优先 (Reference Priority)**:
当 `reference_images` 和 `keyframe_images` 同时传入时，`reference_images` 优先，忽略 `keyframe_images`。`mode` 参数始终由图片参数自动推断，显式传入的 `mode` 被覆盖。
_Avoid_: 合并模式、混合模式
