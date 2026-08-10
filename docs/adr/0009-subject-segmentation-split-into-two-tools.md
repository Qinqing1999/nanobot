# 主体分割工具拆分为 segment_subject + apply_mask 两个独立工具

## Status

Accepted

## Context

主体提取流程包含两个逻辑步骤：（1）调用 BiRefNet 生成分割蒙版，（2）用蒙版裁剪原图生成干净参考图。可以合并为一个工具（extract_subject），也可以拆分为两个独立工具。

## Decision

拆分为两个独立工具：`segment_subject`（生成分割蒙版）和 `apply_mask`（用蒙版裁剪原图）。agent 自行编排调用顺序。

## Considered Options

- **合并为一个工具**（extract_subject）：内部完成分割+裁剪，直接输出干净参考图。否决原因：蒙版无法被 agent 单独复用（如后续 Part A 的蒙版局部编辑功能、或自定义裁剪逻辑）。
- **拆分为两个工具**（采纳）：蒙版作为独立中间产物暴露给 agent，agent 可灵活编排——跳过裁剪直接用蒙版做 inpainting、对蒙版做后处理后再裁剪、或多次裁剪同一蒙版。
- **三个工具**（segment + apply_mask + mask_preview）：否决原因：蒙版可视化不是核心能力，当前不需要。

## Consequences

- agent 需要两次工具调用完成主体提取，比合并方案多一轮 LLM 交互。
- 蒙版作为中间产物通过文件路径传递（非制品注册表），agent 需在同一会话内使用。
- 未来 Part A（蒙版编辑）可直接复用 `segment_subject` 工具生成蒙版，无需重复实现分割逻辑。
