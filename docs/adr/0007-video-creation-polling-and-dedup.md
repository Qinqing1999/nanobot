# 视频创建：限速轮询、错误区分与任务去重

## 状态

已接受

## 背景

Agnes Video API 在高频调用时返回 HTTP 429。当前实现存在三个问题：

1. **错误消息不区分限速与配额耗尽**：`create_task()` 在所有 429 场景下都抛出 `VideoGenerationError("quota exceeded")`，工具层和 LLM 无法区分「等一下再试」和「配额用完了」。
2. **创建失败即终止**：遇到持续限速时，provider 的 3 次快速重试（2s 退避）耗尽后直接抛出错误，工具层返回 `ToolResult.error`，LLM 收到模糊错误消息后可能编造原因（如「每分钟只能请求 2 次」）。
3. **无重复调用防护**：`AgentRunner` 以 `concurrent_tools=True` 并发执行同一 LLM 响应中的多个工具调用。LLM 可能在同一秒内发出两个参数完全相同的 `generate_video` 调用，也可能在后续 turn 中重复调用，导致用户收到重复视频。

## 决策

### 1. `VideoGenerationError` 增加 `kind` 字段

```python
class VideoGenerationError(Exception):
    kind: str  # "rate_limit" | "quota_exhausted" | "unknown"
```

- `create_task()` 的可重试 429（限速）在 3 次重试耗尽后抛出 `kind="rate_limit"`
- 不可重试 429（配额耗尽）立即抛出 `kind="quota_exhausted"`
- 其他错误（超时、网络）抛出 `kind="unknown"`

### 2. 创建轮询（非阻塞）

当 `create_task()` 抛出 `kind="rate_limit"` 时，工具层不立即返回错误，而是：

1. 通过 `schedule_background()` 启动后台创建轮询任务
2. 立即返回 `ToolResult`：`"遇到限速，正在后台持续重试创建，最长 3 分钟，完成后自动通知你。"`
3. 后台任务每 15 秒重试 `create_task()`，墙钟超时 3 分钟
4. 创建成功 → 自动进入现有的 `_poll_and_deliver` 状态轮询流程
5. 创建失败（超时或配额耗尽）→ 通过 `MessageBus.publish_outbound()` 推送 `OutboundMessage` 通知用户

Provider 的 3 次快速重试（2s 退避）保留不变，处理瞬时抖动。工具层的 15s 创建轮询处理持续限速。两者互补。

### 3. 配额耗尽立即失败

`kind="quota_exhausted"` 不进入创建轮询。直接推送 `OutboundMessage` 通知用户原因，返回 `ToolResult` 告知 LLM 配额耗尽。

### 4. 任务去重

模块级 dict `_pending_video_tasks: dict[str, _PendingTask]` 跟踪进行中的视频任务。去重键：

```
hash(session_key, prompt, resolved_image_value, final_mode, width, height, num_frames, frame_rate)
```

不含 `seed`、`negative_prompt`、`num_inference_steps`（影响质量/随机性，不影响内容语义）。URL 和文件路径按字符串字面值比较，不做内容级别去重（过度工程）。

生命周期：从首次注册到终端状态（交付/失败/超时），`try/finally` 保证清理。重启丢失（与 ADR-0002 一致）。

去重命中时，返回已有任务信息（不创建新任务）：
- `creating` 状态：`"已有一个相同的视频任务正在创建中（遇到限速，后台持续重试），请勿重复调用。"`
- `polling` 状态：`"已有一个相同的视频任务正在生成中，任务 ID: {video_id}，请勿重复调用。"`

### 5. 通知路径

所有失败通知通过 `MessageBus.publish_outbound()` 直接推送给用户（路径 A），不经过 LLM。LLM 收到的 `ToolResult` 只包含简要状态，不包含 `response.text` 原文，避免 LLM 编造原因。

### 6. 不处理用户取消

创建轮询或后台轮询期间用户发新消息（如「算了不要了」）不会取消已启动的后台任务。视频生成完成后仍会推送给用户，用户自行忽略。取消机制增加复杂度但收益有限——视频一旦提交到 API 即无法撤回。

## 考虑过的替代方案

- **阻塞式创建轮询**：在 `execute()` 中死等 3 分钟。简单，但阻塞 agent 主循环，用户无反馈。
- **客户端限速（token bucket）**：在 provider 或工具层实现请求频率限制。被否决——限速应由服务端控制，客户端限速是猜测性行为。
- **去重键释放策略 A（创建成功即释放）**：无法防止跨 turn 重复调用。
- **去重状态持久化到 Session**：去重是运行时防护，不是业务状态，不应持久化。
- **移除 provider 的 3 次重试，全部由工具层处理**：provider 的快速重试处理瞬时抖动更高效（2s vs 15s），两者互补优于单一层。

## 后果

- `VideoGenerationError` 签名变更（新增 `kind` 参数），需更新 provider 测试。
- ADR-0002 第 8 条（429 重试：最多 3 次）被本 ADR 的创建轮询机制补充——provider 的 3 次重试保留，工具层在其上叠加 15s/3min 创建轮询。
- 模块级 `_pending_video_tasks` dict 无大小上限，但终端状态自动清理，且受限于并发视频任务数（小规模）。
- 创建轮询期间无 `video_id`，LLM 无法调用 `check_video`。ToolResult 措辞需明确告知 LLM 不要重复调用。
- 创建轮询成功后，后台任务需推送「开始」通知（含 `video_id`），使用户后续可通过 `check_video` 查询进度。
- 用户无法取消已提交的视频任务。
