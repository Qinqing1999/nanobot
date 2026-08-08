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

**进度提示 (Progress Hint)**:
在工具执行期间或后台任务运行期间，向用户频道推送的状态提示消息。视频生成采用"开始 → 50% → 完成"三阶段推送。
_Avoid_: 日志、调试输出

**会话切换 (Session Switching)**:
用户通过 `/sessions` 和 `/switch` 命令在同一 channel 内查看、新建和切换不同会话的能力。通过 `session_key_override` 实现。
_Avoid_: 多租户、频道切换
