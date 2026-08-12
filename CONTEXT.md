# CONTEXT.md — 领域术语表

## 微信通道非阻塞化相关术语

### 核心概念

| 术语 | 定义 | 英文 |
|------|------|------|
| **会话锁** | Per-session 的 asyncio.Lock，确保同一用户的消息串行处理 | Session Lock |
| **Fork** | 从当前 Session 创建快照，用于并行任务的独立上下文 | Session Fork |
| **Merge** | 将并行任务的结果按完成顺序追加回主 Session | Session Merge |
| **意图分类器** | LLM 驱动的轻量判断模块，分析新消息与活跃任务的关系 | Intent Classifier |
| **确认回执** | 消息入队后立即返回给用户的轻量反馈（可选） | Acknowledgment |
| **任务票据** | 每个异步处理任务的唯一标识符，用于追踪和取消 | Task Ticket |

### 任务状态机

```
                    ┌──────────────┐
     入队 ─────────→│   QUEUED     │
                    └──────┬───────┘
                           │ Fork Session
                    ┌──────▼───────┐
                    │   RUNNING    │◄──────────┐
                    └──────┬───────┘           │
                           │                   │
              ┌────────────┼────────────┐      │
              ▼            ▼            ▼      │
         ┌──────────┐ ┌──────────┐ ┌────────┴┐
         │COMPLETED │ │CANCELLED │ │MERGED   │
         └────┬─────┘ └──────────┘ └─────────┘
              │
              ▼
         ┌──────────┐
         │ PUSHED   │ (结果已推送给用户)
         └──────────┘
```

### 意图分类结果

| 分类 | 描述 | 处理行为 |
|------|------|---------|
| `NEW_TASK` | 全新任务 | 创建新的 Fork 并行执行 |
| `CANCEL_PREV` | 取消前一个任务 | 取消活跃任务，创建新任务 |
| `MODIFY_PREV` | 修改前一个任务的参数 | 取消原任务，用新参数重新创建 |
| `APPEND_CONTEXT` | 为前一个任务补充信息 | 合并到现有任务的 pending queue |

### 微信通道特有约束

| 约束 | 值/描述 | 影响 |
|------|---------|------|
| API 限流 | ~7 条消息 / 5 分钟 | 结果推送需要合并/缓冲 |
| 无增量流式 | iLink 不支持 delta 推送 | 只能一次性发送完整回复 |
| Context Token 过期 | ~90-160s 空闲后过期 | 长时间任务需要主动刷新 |
| Long-poll 轮询 | 默认 35s 超时 | 消息接收有天然延迟 |

### 配置项

| 配置键 | 类型 | 默认值 | 描述 |
|--------|------|--------|------|
| `nonblocking.enabled` | bool | false | 是否启用非阻塞模式 |
| `nonblocking.max_concurrent` | int | 3 | 单用户最大并行任务数 |
| `nonblocking.intent_classifier_model` | string | 继承主模型 | 意图分类使用的 LLM 模型 |
| `nonblocking.ack_template` | string | "" | 确认消息模板（空=不发送） |
| `nonblocking.merge_strategy` | enum | "append" | 合并策略: append/last-wins |
