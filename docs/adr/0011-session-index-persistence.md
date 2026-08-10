# 会话索引追踪器持久化

## Status

Accepted

## Context

`AgentLoop._session_indices` 是一个内存字典，记录每个 `base_key`（`channel:chat_id`）当前活跃的会话索引。`_effective_session_key` 在路由消息时查询此字典，以决定是否在 session key 末尾附加 `:N` 后缀。

问题：该字典是纯内存态，从不持久化。服务器重启后字典清空，所有消息路由回索引 0（默认会话，即最旧的上下文）。`/new` 也从索引 0 开始计数，而非接着已有的最高索引继续。

Grilling 中确认的需求和接受的限制：

1. **普通消息和 `/new` 都应路由到最高索引**——重启后不应回退到 session 0。
2. **`/new` 创建 max+1**——不填补空缺（如磁盘上有 0, 1, 3，新建 4）。
3. **所有通道通用**——非微信特有。
4. **只有 session 0 时不变化**——无多会话则保持默认行为。
5. **不向后兼容**——旧安装无 `session_indices.json`，重启后从空开始（与当前行为一致，无回退）。
6. **定期 flush**——纯内存更新 + 定期写入磁盘；崩溃时可能丢失最后一次变更（已接受）。
7. **惰性删除检查**——路由时如果目标 session 不存在，扫描该 `base_key` 的磁盘 session 文件找次高索引。

## Decision

将 `_session_indices` 持久化到 `session_indices.json` 文件，位于 sessions 目录下。

- **启动时**：加载 `session_indices.json`。文件不存在则空字典（无向后兼容扫描）。
- **运行时**：`/new` 和 `/switch` 更新内存字典，通过 `AgentLoop.run()` 的 idle tick 定期 flush 到磁盘。
- **惰性删除检查**：`_effective_session_key` 发现目标 session 文件不存在时，扫描该 `base_key` 下所有 session 文件，回退到最高存在的索引。如果没有任何带索引的 session 存在，回退到 0。

## Considered Options

- **始终扫描磁盘推断最高索引**：磁盘是 source of truth，自动处理删除和向后兼容。被否——用户选择文件持久化方案，接受不向后兼容和定期 flush 的崩溃丢失风险。
- **每次 `/new` 或 `/switch` 立即 flush**：零数据丢失。被否——用户选择定期 flush，接受可能的崩溃丢失。
- **启动时全量扫描 + 文件缓存**：兼顾兼容性和性能。被否——用户明确选择"只加载文件，不扫描"。

## Consequences

- 重启后 `_session_indices` 从文件恢复，消息正确路由到最高索引的 session。
- 旧安装（无文件）行为不变——从空字典开始，路由到 session 0。
- 两次 flush 之间的崩溃会丢失最后一次 `/new` 或 `/switch` 的索引变更。
- session 文件被手动删除后，惰性检查确保回退到次高索引，不会路由到空 session。
