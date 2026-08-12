# Spec: 微信通道非阻塞异步处理 (WeChat Non-Blocking Async)

> **状态**: Ready for Agent  
> **ADR**: [ADR-0014](../adr/0014-weixin-nonblocking-async.md)  
> **领域术语**: [`CONTEXT.md`](../../CONTEXT.md)

---

## Problem Statement

作为微信用户，当前通过 nanobot 微信通道与 AI 助手交互时面临**同步阻塞问题**：

1. **长时间无反馈**: 发送消息后（特别是复杂任务如代码生成、数据分析），需要等待 30 秒到数分钟才能收到回复。期间无法确认系统是否收到消息或正在处理。
2. **无法快速修正**: 如果发送消息后发现描述有误，想立即发送修正指令（"等一下，换成另一个数据集"），必须等待当前任务完全结束后才能被处理。
3. **消息排队延迟**: 同一用户的后续消息被 per-session 串行锁阻塞，即使新消息可能使当前任务变得无关。

**根因**: `WeixinChannel._process_message()` 调用 `_handle_message()` 后同步阻塞等待 AgentLoop 完整处理（包括 LLM 调用、工具执行、响应生成），且 AgentLoop 使用 per-session `asyncio.Lock` 保证串行执行。

## Solution

实现**非阻塞异步处理架构**：

- **即时入队 + 后台并行**: 消息到达后立即入队返回，后台 Fork Session 快照并行执行
- **LLM 驱动的智能合并**: 使用轻量 LLM 调用分析新消息与活跃任务的关系，自动识别取消/修改/补充意图
- **结果追加合并 (Append-All)**: 所有完成的任务结果按完成顺序追加回主 Session，依靠 AutoCompact 控制历史大小
- **尽力推送**: 任务完成后一次性推送结果给用户，失败则丢弃（不重试）

### 用户交互流程示例

```
时间    用户操作              系统行为
────    ─────────             ─────────
10:00   "帮我分析数据A"       → 静默入队 → Fork Task A → 后台执行...
10:00:03 "等一下，换成数据B"  → LLM判断: MODIFY_PREV → 取消Task A → Fork Task A'...
10:00:06 "算了，帮我写报告"  → LLM判断: CANCEL_PREV+NEW → 取消Task A' → Fork Task B...
10:00:45 (30s后)           → 推送: "报告已生成：..."  ← 用户只收到最终结果
```

## User Stories

### 核心体验

1. As a **微信用户**, I want **发送消息后立即得到确认**（可选静默），so that 我知道系统已接收我的请求。
2. As a **微信用户**, I want **在等待复杂任务时能继续发送新消息**，so that 我不需要空等到当前任务结束才能提出新需求。
3. As a **微信用户**, I want **快速发送修正指令时系统能自动识别并切换到新任务**，so that 我不需要等待无关任务完成后再重新描述需求。
4. As a **微信用户**, I want **为正在运行的任务补充信息时能被正确合并**（"还有，加上XXX"），so that 我不需要重复之前的上下文。

### 并发与隔离

5. As a **微信用户**, I want **同一用户的多个独立请求能并行处理**，so that 我的后续请求不被前一个长任务阻塞。
6. As a **系统**, I want **每个并行任务使用独立的 Session 快照 (Fork)**，so that 并行任务之间不会互相污染上下文历史。
7. As a **微信用户**, I want **所有并行任务的最终结果都能保留在我的对话历史中**，so that 我可以回顾完整的交互过程。
8. As a **管理员**, I want **可配置单用户最大并行任务数**（默认 3），so that 系统资源不会被单一用户耗尽。

### 意图智能

9. As a **微信用户**, I want **当我发送"算了""取消""不用了"时系统能自动取消上一个任务**，so that 我不需要等待一个我不再关心的结果。
10. As a **微信用户**, I want **当我发送"换成""改成""不对，用..."时系统能用新参数重新开始任务**，so that 我可以快速修正输入而不丢失对话上下文。
11. As a **开发者**, I want **意图分类由 LLM 驱动而非简单规则匹配**，so that 系统能理解自然语言中的各种表达方式。
12. As a **开发者**, I want **意图分类失败时安全降级为 NEW_TASK**（创建新并行任务），so that 用户消息永远不会被静默丢弃。

### 结果交付

13. As a **微信用户**, I want **任务完成后尽快收到完整结果推送**，so that 我不需要主动查询任务状态。
14. As a **微信用户**, I want **如果结果推送失败（网络问题/限流）不影响后续消息处理**，so that 系统不会因单次推送失败而阻塞。
15. As a **微信用户**, I want **长回复能被正确分段发送**（遵守微信 4000 字符限制），so that 即使是很长的生成内容也能完整送达。

### 可观测性与控制

16. As a **运维人员**, I want **能看到当前活跃的异步任务列表和状态**，so that 我能诊断性能问题和异常状态。
17. As a **开发者**, I want **每个异步任务有唯一的 task_id 用于日志追踪**，so that 我能在日志中关联特定请求的完整生命周期。
18. As a **管理员**, I want **能通过配置开关启用/禁用非阻塞模式**（`nonblocking.enabled`），so that 出问题时能快速回退到原有行为。
19. As a **管理员**, I want **能配置是否发送确认回执及确认消息模板**，so that 我能根据用户群体偏好调整交互方式。

### 兼容性

20. As a **现有用户**, I want **禁用非阻塞模式时行为与当前完全一致**，so that 升级不会改变不想要此功能的用户体验。
21. As a **其他通道的开发者**, I want **非阻塞组件可复用到其他通道**（Feishu/Telegram 等），so that 不需要为每个通道重复实现。

## Implementation Decisions

### 架构组件

#### AsyncTaskCoordinator（新增模块）

- **职责**: Per-channel 异步任务协调器，管理任务生命周期
- **位置**: `nanobot/channels/weixin/nonblocking.py`（或 `nanobot/agent/nonblocking.py` 如需跨通道复用）
- **核心接口**:
  - `submit(msg, session_key) -> task_id`: 提交新任务，触发意图分类和 Fork
  - `cancel(task_id) -> bool`: 取消指定任务
  - `get_active_tasks(session_key) -> list[AsyncTask]`: 查询某用户的活跃任务
  - `on_task_completed(task_id, result)`: 任务完成回调，触发 Merge 和推送

#### IntentClassifier（新增模块）

- **职责**: LLM 驱动的轻量意图分类器
- **位置**: 同上文件或独立的 `nanobot/agent/intent_classifier.py`
- **分类结果枚举**: `IntentAction.NEW_TASK | CANCEL_PREV | MODIFY_PREV | APPEND_CONTEXT`
- **LLM 调用特征**:
  - 使用低 token 预算（~100 input tokens, ~50 output tokens）
  - 可配置模型选择（默认继承主模型，可指定更快的小模型）
  - 分类失败时降级为 `NEW_TASK`
  - 无活跃任务时直接返回 `NEW_TASK`（不调用 LLM）

#### SessionForkManager（新增模块）

- **职责**: 管理 Session 的 Fork（快照创建）和 Merge（结果合并）
- **位置**: `nanobot/session/fork_manager.py`
- **Fork 行为**:
  - 深拷贝 `session.messages` 列表
  - 深拷贝 `session.provider_state`（如果存在）
  - 记录 fork 时间戳和源 task_id
  - **不拷贝 metadata 中的临时字段**（checkpoint、pending_user_turn）
- **Merge 行为** (Append-All 策略):
  - 乐观锁：检查 merge 时 session.messages 的 base version 是否匹配
  - 追加新的 assistant 消息到 session.messages
  - 更新 provider_state（如果有且版本更新）
  - 调用 `session.save()` 持久化
  - 冲突时重试或强制追加（可配置）

#### WeixinConfig 扩展

- **新增字段**:
  ```python
  class NonblockingConfig(BaseModel):
      enabled: bool = False
      max_concurrent: int = 3
      intent_classifier_model: str = ""  # 空=继承主模型
      ack_template: str = ""  # 空=不发送确认
      merge_strategy: Literal["append", "last_wins"] = "append"
  
  class WeixinConfig(Base):
      # ... 现有字段 ...
      nonblocking: NonblockingConfig = NonblockingConfig()
  ```

#### WeixinChannel._process_message 改造

- **改动点**: 在现有逻辑之前插入非阻塞分支
- **条件判断**: `if self.config.nonblocking_enabled:` 进入非阻塞路径
- **非阻塞路径**:
  1. 解析消息内容（保持现有解析逻辑不变）
  2. 调用 `coordinator.submit()` 入队（立即返回）
  3. 可选：发送确认回执
  4. **return**（不调用 `_handle_message`）
- **阻塞路径**: 完全保持现有逻辑（向后兼容）

#### AgentLoop 接口暴露

- **新增方法**:
  - `fork_session(session_key) -> SessionSnapshot`: 为外部调用者提供 Session Fork 能力
  - `merge_result(session_key, snapshot, messages) -> None`: 提供结果合并能力
- **设计理由**: Coordinator 需要在 AgentLoop 锁之外操作 Session，必须通过显式接口

### 数据流

```
WeixinChannel._process_message()
  │
  ├── [nonblocking=False] ──→ _handle_message() → MessageBus → AgentLoop._dispatch() (原有)
  │
  └── [nonblocking=True]  ──→ coordinator.submit()
                              ├── IntentClassifier.classify(msg, active_tasks)
                              ├── 根据 IntentAction 处理:
                              │   ├── CANCEL_PREV/MODIFY_PREV → asyncio.Task.cancel()
                              │   └── APPEND_CONTEXT → 注入 pending queue
                              ├── SessionForkManager.fork(session)
                              ├── asyncio.create_task(_execute_task(task))
                              └── return task_id (立即返回)
                                      
_async_execute_task(task):
  │
  ├── 构造临时 InboundMessage
  ├── bus.publish_inbound(temp_msg)
  ├── 等待 bus.consume_outbound() (带超时)
  ├── 收到 OutboundMessage 后:
  │   ├── SessionForkManager.merge(session, snapshot, result)
  │   └── channel.send(result)  ← 尽力推送
  └── 清理 task 状态
```

### 任务状态机

```python
class TaskState(Enum):
    QUEUED = "queued"       # 已入队，等待 Fork
    FORKING = "forking"     # 正在创建 Session 快照
    RUNNING = "running"     # 正在执行（AgentLoop 处理中）
    MERGING = "merging"     # 正在合并结果到主 Session
    PUSHING = "pushing"     # 正在推送给用户
    COMPLETED = "completed"  # 全部完成
    CANCELLED = "cancelled"  # 被后续消息取消
    FAILED = "failed"        # 执行失败

class AsyncTask:
    task_id: str
    msg: InboundMessage
    session_snapshot: SessionSnapshot
    intent: IntentResult
    state: TaskState
    created_at: float
    completed_at: float | None
    result: OutboundMessage | None
    error: str | None
```

## Testing Decisions

### 测试 Seam 设计

**主要 Seam**: `AsyncTaskCoordinator` 类（高层 seam）

测试应通过 mock 以下依赖来隔离：
- `IntentClassifier` → mock 返回预定义的 `IntentResult`
- `SessionForkManager` → mock fork/merge 操作
- `AgentLoop.fork_session` / `AgentLoop.merge_result` → mock Session 操作
- `WeixinChannel.send` / `WeixinChannel._send_text` → mock 推送操作

**Seam 选择理由**:
- Coordinator 是唯一的新增公共接口
- 所有复杂逻辑（意图分类、Fork/Merge、LLM 调用）都通过依赖注入进入 Coordinator
- 测试 Coordinator 等于端到端验证整个非阻塞流程

### 测试模块清单

| 模块 | 测试重点 | Prior Art |
|------|---------|----------|
| `test_nonblocking_coordinator.py` | 任务提交/取消/完成生命周期 | `tests/agent/test_runner_injections.py` (pending queue 模式) |
| `test_intent_classifier.py` | 意图分类准确性（mock LLM） | `nanobot/channels/weixin/tests/test_weixin_channel.py` (消息处理模式) |
| `test_session_fork_merge.py` | Fork 深拷贝正确性、Merge 冲突处理 | `tests/agent/test_session_lock_lifecycle.py` (Session 生命周期) |
| `test_weixin_nonblocking.py` | 集成测试：_process_message 非阻塞分支 | `test_weixin_channel.py::test_process_message_deduplicates_inbound_ids` |

### 好测试的定义

1. **只测 external behavior**: 验证 coordinator.submit() 返回 task_id、任务最终完成/取消、结果被推送——不验证内部 asyncio 调度细节
2. **不测 implementation details**: 不断言具体的事件循环顺序、task 创建数量等
3. **Mock LLM 调用**: IntentClassifier 测试使用 mock 返回值，不发起真实 LLM 请求
4. **并发安全**: 多个测试验证同一 session_key 的并发 submit 正确性（但不依赖精确时序）

### 关键测试场景

1. **单任务正常流程**: submit → RUNNING → COMPLETED → 结果推送
2. **取消意图**: submit(A) → submit(B, CANCEL_PREV) → A 取消 → B 完成
3. **修改意图**: submit(A) → submit(B, MODIFY_PREV) → A 取消 → B 用新参数执行
4. **补充上下文**: submit(A) → submit(B, APPEND_CONTEXT) → B 合入 A 的 pending queue
5. **最大并发限制**: 超过 max_concurrent 时 submit 抛出异常或排队
6. **LLM 降级**: IntentClassifier 异常时降级为 NEW_TASK
7. **Session Merge 冲突**: 两个任务同时完成时的 merge 竞态处理
8. **推送失败**: send() 异常不影响任务状态标记为 COMPLETED
9. **配置关闭**: nonblocking_enabled=False 时走原有阻塞路径
10. **确认回执**: ack_template 非空时发送确认消息

## Out of Scope

1. **WebUI 异步任务状态展示**: 本 spec 只关注后端处理逻辑，前端展示作为后续迭代
2. **任务持久化和恢复**: 服务重启后恢复进行中的任务不在本阶段实现
3. **跨通道通用化**: 初期只实现微信通道，其他通道（Feishu/Telegram）的适配作为 Phase 3
4. **分布式部署支持**: 单实例内存级 Coordinator，不支持多实例协调
5. **优先级队列**: 所有任务同等优先级，不支持用户自定义优先级
6. **任务超时自动取消**: 不自动取消长时间运行的任务（由 AgentLoop 自身的 timeout 机制覆盖）
7. **结果多通道回退**: 微信推送失败时不尝试邮件/Webhook 等备用渠道

## Further Notes

### 与现有 ADR 的关系

- **ADR-0011 (Session Index Persistence)**: Fork/Merge 必须正确处理 session index，确保 Fork 的快照使用正确的 index
- **ADR-0013 (Apply-Mask Auto-Delivery)**: 非阻塞模式下 mask 自动送达仍应在任务完成后正常工作
- **Session AutoCompact**: Append-All 策略依赖 AutoCompact 控制 history 大小，避免无限增长

### 性能考虑

- **Fork 开销**: 深拷贝 messages 列表（O(n), n=历史消息数）。对于大 history 场景（>500 条），考虑 copy-on-write 优化
- **LLM 分类延迟**: 每次 submit 增加 ~100-500ms（取决于模型速度）。可通过批量分类或缓存优化
- **内存占用**: 每个 Fork 持有一份独立 messages 副本。max_concurrent=3 时最多 3 份副本

### 安全边界

- **权限检查**: 非阻塞模式下仍须执行 `is_allowed()` 检查
- **Context Token 管理**: Fork 的任务使用 fork 时刻的 context_token，过期时刷新
- **速率限制**: 并行任务的结果推送受微信 API 限流约束，需合并/缓冲

### 配置完整示例

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "allow_from": ["*"],
      "nonblocking": {
        "enabled": true,
        "max_concurrent": 3,
        "intent_classifier_model": "",
        "ack_template": "",
        "merge_strategy": "append"
      }
    }
  }
}
```
