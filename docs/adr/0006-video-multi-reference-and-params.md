# 视频生成：多图参考、关键帧模式与参数完善

## 状态
已接受

## 背景

Agnes Video V2.0 API 支持多图参考（最多 4 张）和关键帧插值（首尾 2 张）两种独立的图片输入模式，通过 `mode` 字段区分。当前实现存在三个问题：

1. **参数结构不统一**：视频工具用 `image`（单字符串）做单图图生视频，用 `keyframe_images`（数组）做关键帧，但缺少多图参考的参数入口。
2. **`extra_body` 传递 bug**：工具层将多图数据通过 `extra_body` 参数传给 provider，provider 把它嵌套到 `body["extra_body"]` 子键下，而非合并到请求体顶层。Agnes API 不识别这个路径，导致关键帧模式实际无法工作。
3. **参数缺口**：`VideoGenerationToolConfig` 缺少 `default_aspect_ratio`；`num_inference_steps` 在 provider 层支持但未暴露到工具层；图片生成和视频生成的 Skill 文档缺少自然语言→参数提取的引导。

## 决策

### 1. 统一图片参数为 `reference_images`

废弃 `image`（单字符串）参数，统一用 `reference_images`（字符串数组）。1 张图也是数组。保留 `keyframe_images`（恰好 2 张，首帧+尾帧）作为独立参数。两者互斥，同时传入时 `reference_images` 优先。

### 2. `mode` 自动推断

`mode` 参数保留但变为可选，由图片参数自动推断：

| 图片参数 | 数量 | 自动推断的 mode |
|---|---|---|
| 无图片 | — | `ti2vid` |
| `reference_images` | 1 | `img2vid` |
| `reference_images` | 2-4 | `multi_reference` |
| `keyframe_images` | 2 | `keyframes` |

显式传入的 `mode` 被图片参数覆盖。

### 3. 修复 Provider 层 `extra_body` bug

`create_task` 的 `image` 参数改为 `str | list[str] | None`，直接设置到 `body["image"]`。`mode` 直接设置到 `body["mode"]`。`extra_body` 参数从嵌套（`body["extra_body"] = extra_body`）改为合并（`body.update(extra_body)`）。

### 4. 新增 Config 和工具参数

- `VideoGenerationToolConfig` 新增 `default_aspect_ratio: str = "16:9"`。
- 工具层暴露 `num_inference_steps`（整数，可选）、`num_frames`（整数，可选，覆盖 duration 预设）、`frame_rate`（整数，可选，覆盖 duration 预设）。
- Duration 预设（3s/5s/10s/18s）保留；`num_frames`/`frame_rate` 优先级高于 duration。

### 5. Artifact ID 解析

视频工具的 `reference_images` 和 `keyframe_images` 支持 artifact ID（如 "1001"）、URL、文件路径三种输入。沿用现有 `_resolve_artifact_id()` 机制。

### 6. Skill 文档更新

- `video-generation/SKILL.md`：更新参数表（`reference_images` 替代 `image`），增加多图参考和关键帧的使用示例，增加 `num_inference_steps` 说明。
- `image-generation/SKILL.md`：增加自然语言→参数提取引导（如"16:9 横版" → `aspect_ratio="16:9"`，"4K 高清" → `image_size="4K"`）。

## 考虑过的替代方案

- **统一为 `images` 数组 + mode 区分语义**：单一参数更简洁，但废弃 `image` 参数是破坏性变更，且 `keyframe_images` 的语义与 `reference_images` 本质不同（时间顺序 vs 无序参考），分开更清晰。
- **保留 `image` 单字符串参数 + 新增 `reference_images`**：向后兼容，但三个图片参数（`image`、`reference_images`、`keyframe_images`）增加 LLM 选择负担。
- **让 `mode` 优先于图片参数**：更灵活但容易出错，LLM 可能传错 mode 导致 API 行为异常。图片参数优先更安全。

## 后果

- `image` 参数废弃是破坏性变更：已发布的 SKILL.md 和工具 schema 中使用了 `image`，需要同步更新。
- Provider 层 `create_task` 签名变更（`image: str | None` → `image: str | list[str] | None`）向后兼容。
- `extra_body` 修复可能影响已有依赖嵌套行为的代码（但当前嵌套行为是 bug，不会有正常依赖）。
- 图片生成工具的 `reference_images` 解析机制（文件路径）与视频工具（artifact ID）不一致，后续需统一，但不在本次范围内。
