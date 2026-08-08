# 制品通知架构：通道层推送与 LLM 解耦

## 状态
已接受

## 背景
制品注册表（ADR 0001）建立了 Session 级别的四位数字 ID 系统。初始实现中，上传文件的 ID 通过 WebSocket 专用事件 `artifacts_registered` 推送，且 `to_context_lines()` 指示 LLM 在回复中包含制品 ID。

这产生了几个问题：
1. WebSocket 事件在非 WebSocket 通道（Telegram、Discord 等）上无法工作。
2. 依赖 LLM 在回复中提及 ID 不可靠——LLM 可能遗漏或格式不一致。
3. 上传和生成两种路径的 ID 推送机制不统一。

## 决策

制品 ID 的通知采用**通道层/工具层推送**模式，与 LLM 完全解耦：

### 1. 统一通知机制
所有通道统一使用 `OutboundMessage` 发送制品通知（不使用 WebSocket 专用事件）。通知在聊天中显示为一条系统消息。

### 2. 上传通知
- **位置**：通道层（如 WebSocket runtime 的 `_dispatch_envelope`）
- **时机**：文件保存并注册后，发布 `InboundMessage` 之前
- **格式**：批量上传时发一条汇总消息
- **内容**：制品 ID、类型、文件名、时间戳

### 3. 生成通知
- **位置**：工具内部（`ImageGenerationTool`、`VideoGenerationTool`、`WriteFileTool`）
- **时机**：文件保存并注册后，工具结果返回给 LLM 之前（选项 A——即时反馈）
- **机制**：工具通过 `MessageBus.publish_outbound()` 直接发送通知

### 4. LLM 上下文
- 保留 `to_context_lines()` 注入制品列表到 agent 上下文，使 LLM 能理解用户引用的 ID 对应什么文件
- 移除"请在回复中包含制品 ID"的指令——ID 已通过独立消息推送

### 5. 制品引用解析
- **位置**：`_restore_turn()` 中的消息预处理
- **算法**：正则提取四位数字 → 查询注册表 → 命中则将文件路径加入 `media`，文本中替换为描述性引用
- **边界**：未命中的四位数字保持原文不动

### 6. 通知消息格式
```
📎 已注册 2 个制品:
  1001 [图片] cat.png | 10:01:03
  1002 [图片] dog.png | 10:01:03
```

## 考虑过的替代方案

- **WebSocket 专用事件**：仅适用于 WebUI，其他通道无法使用。已放弃。
- **LLM 负责提及 ID**：不可靠，LLM 可能遗漏或格式不一致。已放弃。
- **Runner 层后置检查**：工具执行完毕后由 runner 统一检查并推送。延迟更大且 runner 需要额外的 bus 访问。不如工具直接推送简单。

## 后果

- 生成文件的工具（`ImageGenerationTool`、`VideoGenerationTool`、`WriteFileTool`）需要 `bus` 参数。
- 所有通道的上传处理需要发布 `OutboundMessage` 通知。
- `_restore_turn()` 需要增加制品引用解析预处理。
- 前端无需处理 `artifacts_registered` 事件——通知作为普通消息显示。
