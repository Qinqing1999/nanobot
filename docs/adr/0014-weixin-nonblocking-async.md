# ADR-0014: 微信通道非阻塞异步处理架构

## 状态

提案中 (Proposed)

## 背景

当前微信通道（WeixinChannel）采用**同步阻塞模式**处理用户消息：

1. 用户发送消息 → `_process_message()` 被调用
2. 调用 `_handle_message()` → 消息发布到 MessageBus
3. **阻塞等待** AgentLoop 完成完整处理（LLM 调用 + 工具执行 + 响应生成）
4. 通过 `send()` 将结果推送给用户
5. 返回后才能处理下一条消息

### 问题症状

| 场景 | 用户体验 |
|------|---------|
| 复杂任务（如代码生成） | 用户等待 30s-几分钟无任何反馈 |
| 快速连发多条消息 | 后续消息被排队，无法及时响应"修改意图" |
| 长时间思考 | 无法知道系统是否收到消息或正在处理 |

### 根因分析

```
runtime.py:1060-1068
┌─────────────────────────────────────────────────┐
│ await self._start_typing(from_user_id, ctx_token) │
│ await self._handle_message(                     │ ← 阻塞点
│     sender_id=from_user_id,                      │
│     chat_id=from_user_id,                        │
│     content=content,                             │
│     ...                                          │
│ )                                                │
└─────────────────────────────────────────────────┘

loop.py:1317
┌─────────────────────────────────────────────────┐
│ async with lock, gate:  ← per-session 串行锁    │
│     pending = asyncio.Queue(maxsize=20)          │
│     self._pending_queues[session_key] = pending   │
│     # ... 整个 _process_message 在锁内完成       │
└─────────────────────────────────────────────────┘
```

## 决策

### 架构目标

实现**非阻塞异步处理**模式，具备以下特性：

1. **即时确认**：消息入队后立即返回（可选静默确认）
2. **并行执行**：支持同一用户的多个任务 Fork Session 并行处理
3. **智能合并**：LLM 驱动的意图判断，自动处理取消/修改指令
4. **结果推送**：完成后尽力推送给用户

### 核心组件

#### 1. AsyncTaskCoordinator（任务协调器）

```python
class AsyncTaskCoordinator:
    """Per-channel 异步任务协调器"""

    def __init__(
        self,
        max_concurrent: int = 3,
        intent_classifier: IntentClassifier | None = None,
    ):
        self._active_tasks: dict[str, AsyncTask] = {}  # task_id → task
        self._session_forks: dict[str, SessionSnapshot] = {}  # session_key → snapshot
        self._max_concurrent = max_concurrent
        self._classifier = intent_classifier

    async def submit(
        self,
        msg: InboundMessage,
        session: Session,
    ) -> str:
        """提交新任务，返回 task_id"""
        # 1. LLM 意图分类
        intent = await self._classify_intent(msg)

        # 2. 根据意图处理活跃任务
        if intent.action == "cancel_prev":
            await self._cancel_active(msg.session_key)
        elif intent.action == "modify_prev":
            await self._cancel_active(msg.session_key)

        # 3. Fork Session
        snapshot = self._fork_session(session)

        # 4. 创建异步任务
        task = AsyncTask(
            task_id=self._generate_id(),
            msg=msg,
            session_snapshot=snapshot,
            intent=intent,
        )
        self._active_tasks[task.task_id] = task

        # 5. 异步执行
        asyncio.create_task(self._execute_task(task))

        return task.task_id
```

#### 2. IntentClassifier（意图分类器）

```python
class IntentAction(Enum):
    NEW_TASK = "new_task"           # 全新任务
    CANCEL_PREV = "cancel_prev"     # 取消前一个任务
    MODIFY_PREV = "modify_prev"     # 修改参数
    APPEND_CONTEXT = "append_context"  # 补充上下文

@dataclass
class IntentResult:
    action: IntentAction
    confidence: float              # 置信度 0-1
    target_task_id: str | None     # 目标任务 ID（如有）
    reasoning: str                 # 分类原因（用于日志）

class IntentClassifier:
    """LLM 驱动的轻量意图分类"""

    SYSTEM_PROMPT = """你是一个消息意图分类器。分析用户的新消息与当前活跃任务的关系。

分类规则:
- CANCEL_PREV: 用户明确表示要停止/放弃上一个任务（"算了""取消""不用了""stop"）
- MODIFY_PREV: 用户想修改上一个任务的输入（"换成""改成""不对，用..."）
- APPEND_CONTEXT: 用户在补充信息（"还有""另外""再加上"）
- NEW_TASK: 完全无关的新请求

只输出 JSON: {"action": "...", "confidence": 0.95, "reasoning": "..."}
"""

    async def classify(
        self,
        new_msg: InboundMessage,
        active_tasks: list[AsyncTask],
    ) -> IntentResult:
        # 构建分类 prompt（包含活跃任务摘要）
        # 调用 LLM（使用快速模型/低 token 限制）
        # 解析结果
        pass
```

#### 3. Session Fork/Merge 机制

```python
@dataclass
class SessionSnapshot:
    """Session 的不可变快照"""
    session_key: str
    messages: list[dict]           # 截止 fork 点的历史
    provider_state: Any | None     # Provider 对话状态
    metadata: dict
    created_at: float
    forked_from_task_id: str | None

class SessionForkManager:
    """管理 Session 的 Fork 和 Merge"""

    def fork(self, session: Session, task_id: str) -> SessionSnapshot:
        """创建 Session 快照"""
        return SessionSnapshot(
            session_key=session.key,
            messages=list(session.messages),  # 深拷贝
            provider_state=copy.deepcopy(session.provider_state),
            metadata=dict(session.metadata),
            created_at=time.time(),
            forked_from_task_id=None,
        )

    async def merge(
        self,
        session: Session,
        snapshot: SessionSnapshot,
        result_messages: list[dict],
        task_id: str,
    ) -> None:
        """将任务结果合并回主 Session

        策略: 全部追加 (Append-All)
        - 所有完成的任务结果按完成顺序追加
        - 依靠 AutoCompact 控制历史大小
        """
        # 检查是否有冲突（其他任务已合并更新了 messages）
        # 追加新的 assistant 消息
        # 更新 provider_state（如果有）
        # 持久化
        pass
```

### 处理流程对比

#### 当前（阻塞模式）

```
用户发消息A
    ↓
[阻塞] start_typing()
[阻塞] handle_message() → MessageBus → AgentLoop._dispatch()
    [获取 session lock]
    [restore/build/run/save/respond]  ← 可能 10s-几分钟
    [释放 session lock]
    ↓
send(reply_A)  ← 用户才看到回复
    ↓
用户发消息B  ← 此时才能处理
```

#### 新模式（非阻塞）

```
用户发消息A
    ↓
[异步] submit(task_A)
    ├── Fork Session → snapshot_A
    ├── 创建 AsyncTask
    └── asyncio.create_task(_execute(task_A))  ← 不阻塞
         ↓
    [立即返回] 可选: send_ack("已接收 #A")
         ↓
用户发消息B（不需要等 A 完成！）
    ↓
[异步] IntentClassifier.classify(msg_B, [task_A])
    ├── 判断为 MODIFY_PREV → cancel(task_A)
    ├── Fork Session → snapshot_B
    └── asyncio.create_task(_execute(task_B))
         ↓
task_B 完成 → merge(Session, snapshot_B, reply_B)
    ↓
send(reply_B)  ← 用户收到最终结果
```

## 考虑过的替代方案

### 方案 A: 纯队列 + 串行处理（Queue-Only）

**描述**: 消息入队后立即确认，但后台仍串行处理

**优点**:
- 无 Session 冲突风险
- 实现最简单

**缺点**:
- 不是真正的并行，长任务仍会阻塞后续消息
- 无法满足"并行处理模式"的需求

**否决原因**: 不满足用户的核心需求

---

### 方案 B: Cancel-Prev（取消前者）

**描述**: 新消息到达时自动取消上一个任务

**优点**:
- 实现简单
- Session 始终干净

**缺点**:
- 丢失中间任务的进度
- 如果用户只是想"补充信息"，会导致重复工作

**否决原因**: 对于"补充信息"场景体验差

---

### 方案 C: 时间窗口聚合（Time-Window Batching）

**描述**: N 秒内的消息批量收集后一次性处理

**优点**:
- 无需 LLM 调用
- 实现简单

**缺点**:
- 引入固定延迟（必须等窗口结束）
- 无法理解语义，可能错误合并独立请求

**否决原因**: 用户明确要求 LLM 驱动的智能判断

---

### 方案 D: Independent Sessions（独立会话）

**描述**: 每个请求创建完全独立的临时 Session

**优点**:
- 完全并行无冲突

**缺点**:
- 历史碎片化
- 后续对话丢失上下文
- 内存开销大

**否决原因**: 上下文断裂对 AI 助手体验影响严重

## 后果

### 正面影响

| 影响 | 描述 |
|------|------|
| 用户体验提升 | 即时反馈 + 并行处理减少等待时间 |
| 意图理解准确 | LLM 驱动的智能合并比规则匹配更精准 |
| 自然交互 | 符合用户"快速修正"的使用习惯 |

### 负面影响

| 影响 | 缓解措施 |
|------|---------|
| 增加复杂度 | 封装在 AsyncTaskCoordinator 中，对其他通道透明 |
| LLM 调用成本 | 意图分类使用小模型/低 token 限制（~100 tokens） |
| Session 合并冲突 | 采用 append-all + version check 乐观合并 |
| 内存占用 | Fork 时深拷贝 messages，限制 max_concurrent 控制上限 |
| 测试复杂度 | 需要 mock IntentClassifier 进行单元测试 |

### 兼容性

| 组件 | 影响程度 | 改动内容 |
|------|---------|---------|
| `WeixinChannel` | **高** | 新增 coordinator 集成 |
| `AgentLoop` | **中** | 暴露 `fork_session` / `merge_result` 接口 |
| `BaseChannel` | **低** | 可选的 nonblocking mixin |
| 其他 Channel | **无** | 默认行为不变，可选择性启用 |
| `Session` | **低** | 新增 `snapshot()` / `merge()` 方法 |
| Config | **低** | 新增 `weixin.nonblocking.*` 配置段 |

### 迁移路径

1. **Phase 1** (MVP): 实现 AsyncTaskCoordinator + 简单关键词分类器（无 LLM）
2. **Phase 2**: 集成 LLM IntentClassifier
3. **Phase 3**: 扩展到其他通道（Feishu/Telegram 等）
4. **配置开关**: `weixin.nonblocking.enabled = false` 时回退到原有行为

## 待决事项

- [ ] 确定 IntentClassifier 的模型选择（主模型 vs 独立小模型）
- [ ] 定义 Task 失败时的重试和通知策略
- [ ] 评估 Fork 深拷贝的性能影响（大 history 场景）
- [ ] 设计 WebUI 中异步任务状态的展示方式
- [ ] 考虑是否需要持久化任务状态（服务重启恢复）

## 参考实现伪代码

### WeixinChannel._process_message 改造

```python
async def _process_message(self, msg: dict[str, Any]) -> None:
    # ... 现有的消息解析逻辑保持不变 ...
    content = "\n".join(content_parts)
    if not content:
        return

    # ===== 新增：非阻塞分支 =====
    if self.config.nonblocking_enabled:
        await self._process_nonblocking(
            from_user_id=from_user_id,
            content=content,
            media=media_paths or None,
            metadata={"message_id": msg_id},
            ctx_token=ctx_token,
        )
        return  # 立即返回，不阻塞

    # ===== 原有逻辑（向后兼容）=====
    await self._start_typing(from_user_id, ctx_token)
    await self._handle_message(...)
```

### 非阻塞处理核心

```python
async def _process_nonblocking(
    self,
    from_user_id: str,
    content: str,
    media: list[str] | None,
    metadata: dict,
    ctx_token: str,
) -> None:
    """非阻塞异步处理入口"""

    # 1. 构造 InboundMessage
    inbound = InboundMessage(
        channel=self.name,
        sender_id=from_user_id,
        chat_id=from_user_id,
        content=content,
        media=media or [],
        metadata=metadata,
    )

    # 2. 提交到协调器（立即返回）
    task_id = await self.coordinator.submit(
        msg=inbound,
        session_key=f"{self.name}:{from_user_id}",
    )

    # 3. 可选：发送静默确认
    if self.config.nonblocking_ack_template:
        ack_msg = self.config.nonblocking_ack_template.format(
            task_id=task_id[:8],
        )
        try:
            await self._send_text(from_user_id, ack_msg, ctx_token)
        except Exception:
            pass  # 尽力推送，失败不阻塞
```
