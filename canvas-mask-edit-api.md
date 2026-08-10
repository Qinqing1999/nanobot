# 图片蒙版编辑与自动主体提取 — API 接口文档

> 本文档面向需要对接或迁移「图片蒙版局部编辑」和「自动主体提取+三视图生成」功能的开发者。仅包含 API 接口规范、参数定义、数据格式和交互流程。

---

## 目录

- [Part A：图片蒙版编辑（Inpainting）](#part-a图片蒙版编辑inpainting)
- [Part B：自动主体提取 + 三视图一致性生成（增强方案）](#part-b自动主体提取--三视图一致性生成增强方案)
- [Part C：对接清单](#part-c对接清单)

---

# Part A：图片蒙版编辑（Inpainting）

## A1. 功能说明

用户在一张图片上涂抹选定区域，并输入文字提示词，AI 只重绘选定区域、保留其余部分不变。

**核心要点：**

- 每次请求需要上传 **2 张图片**到上游 AI API：
  - **原图**：用户要编辑的那张图片
  - **蒙版图**：黑白图，白色标记需要 AI 重绘的区域，黑色标记保留原图的区域
- 附带一段 **文字提示词**，指导 AI 在选定区域生成什么内容
- 走 **异步任务模式**：创建任务返回 task_id，通过轮询或 SSE 获取最终结果

---

## A2. 交互流程

```
1. 用户在图片上涂抹选定区域 + 输入提示词
2. 前端生成蒙版图（黑白 PNG base64）
3. 前端调用「创建图片任务」API，提交：原图 + 蒙版 + 提示词
4. 后端立即返回 task_id（状态=pending）
5. 后端异步调用上游 AI API 执行局部重绘
6. 前端轮询「查询任务状态」API 或通过 SSE 接收结果
7. 任务完成，前端拿到结果图片 URL，更新展示
```

---

## A3. API 接口

### A3.1 创建图片任务

```
POST /api/images/tasks
```

**Content-Type:** `application/json`

#### 请求参数

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `prompt` | string | 是 | - | 提示词，描述要在选定区域生成什么内容 |
| `model` | string | 否 | `""` | 模型 ID |
| `size` | string | 否 | `1024x1024` | 输出图片尺寸，格式 `宽x高` |
| `response_format` | string | 否 | `url` | 响应格式：`url` 或 `b64_json` |
| `mode` | string | 否 | `null` | `image2image`（蒙版编辑走此模式）。也可不传，后端根据是否携带参考图自动判断 |
| `base64_images` | string[] | 否 | `null` | **原图 base64 数组**。蒙版编辑场景传 1 张原图。元素为 `data:image/xxx;base64,xxxx` 格式的 data URI 或纯 base64 字符串 |
| `image_urls` | string[] | 否 | `null` | 原图 URL 数组（公网可访问）。与 `base64_images` 二选一或混用均可 |
| `mask` | string | 否 | `null` | **蒙版图 base64 data URI**。黑白 PNG，白色=编辑区域，黑色=保留区域 |

#### 最简请求示例

```json
{
  "prompt": "把背景换成星空",
  "model": "agnes-image-2.1-flash",
  "size": "1024x1024",
  "response_format": "url",
  "base64_images": [
    "data:image/png;base64,iVBORw0KGgo..."
  ],
  "mask": "data:image/png;base64,iVBORw0KGgo..."
}
```

#### 响应

**HTTP 200 — 任务创建成功**

```json
{
  "task_id": "img_1784000000000_abc12345",
  "status": "pending",
  "prompt": "把背景换成星空",
  "model": "agnes-image-2.1-flash",
  "size": "1024x1024",
  "message": "任务已创建，请使用 GET /api/images/tasks/{task_id} 轮询状态"
}
```

---

### A3.2 查询任务状态

```
GET /api/images/tasks/{task_id}
```

#### 响应

**任务进行中**

```json
{
  "task_id": "img_1784000000000_abc12345",
  "status": "pending",
  "progress": 30
}
```

**任务成功**

```json
{
  "task_id": "img_1784000000000_abc12345",
  "status": "success",
  "progress": 100,
  "result_url": "https://cdn.example.com/result.png"
}
```

**任务失败**

```json
{
  "task_id": "img_1784000000000_abc12345",
  "status": "failed",
  "error": "错误信息"
}
```

---

### A3.3 取消任务

```
DELETE /api/images/tasks/{task_id}
```

#### 响应

```json
{
  "success": true,
  "task_id": "img_1784000000000_abc12345",
  "status": "cancelled"
}
```

---

## A4. 蒙版图格式规范

### 基本要求

| 属性 | 要求 |
|------|------|
| 图片格式 | PNG |
| 编码方式 | base64 data URI，即 `data:image/png;base64,xxxx` |
| 尺寸 | 必须与原图尺寸一致（宽×高像素数完全相同） |

### 颜色语义

| 颜色 | 像素值 | 含义 |
|------|--------|------|
| **黑色** | `#000000` (RGB 0,0,0) | **保留区域** — 该位置保持原图不变 |
| **白色** | `#FFFFFF` (RGB 255,255,255) | **编辑区域** — 该位置由 AI 根据 prompt 重绘 |

### 蒙版与原图的对应关系

```
原图                          蒙版图                        结果
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│                  │         │██████████████░░░░│         │                  │
│   一只猫在草地上  │   +     │██████████████░░░░│   →     │   一只猫在星空下  │
│                  │         │██████░░░░████████│         │                  │
│                  │         │██████░░░░████████│         │                  │
│                  │         │██████████████████│         │                  │
└──────────────────┘         └──────────────────┘         └──────────────────┘
                             白色=AI重绘区域               黑色区域保持原图
```

### 蒙版形状不限于矩形

蒙版可以是**任意形状**——只要在黑白 PNG 中用白色标记出需要编辑的像素区域即可。矩形蒙版只是最简单的形式，主体轮廓形状的蒙版效果更好（见 Part B）。

### 前端生成蒙版的方式

1. 创建一个与原图尺寸相同的 canvas
2. 用黑色 `#000000` 填充整个画布
3. 用白色 `#FFFFFF` 在需要编辑的区域绘制（画笔涂抹、多边形填充、或自动分割结果均可）
4. 调用 `canvas.toDataURL('image/png')` 生成 base64 data URI

---

## A5. 上游 AI API 请求结构

后端收到前端请求后，构建以下结构发送给上游 AI 图片生成 API：

```
POST {上游API地址}/images/generations
```

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "把背景换成星空",
  "size": "1024x1024",
  "extra_body": {
    "image": [
      "data:image/png;base64,iVBORw0KGgo..."
    ],
    "response_format": "url",
    "mask": "data:image/png;base64,iVBORw0KGgo..."
  }
}
```

**关键约束：** `image`、`mask`、`response_format` 必须放在 `extra_body` 中，放在顶层会导致上游 API 返回 400 错误。

---

# Part B：自动主体提取 + 三视图一致性生成（增强方案）

## B1. 功能说明

用户说"帮我生成这个人的三视图"，系统自动完成：

1. **自动识别图片中的主体**（真人/动漫均支持），生成像素级主体形状蒙版（非矩形）
2. 用蒙版裁剪出干净的主体图（去掉背景干扰）
3. 以主体图为参考，生成正面/侧面/背面三视图，最大程度保证主体一致性

**解决的问题：**

- 手动涂抹蒙版精度低、操作繁琐
- 带背景的原图作为参考图时，AI 容易被背景干扰导致主体不一致
- 三视图独立生成时角色外貌容易"漂移"

---

## B2. 整体流程

```
用户："帮我生成这个人的三视图"
    ↓
① 自动分割微服务（u2netp ONNX）
   输入：原图 base64
   输出：像素级主体形状蒙版（黑白 PNG base64）
    ↓
② 主体裁剪
   用蒙版从原图裁剪出主体（去掉背景）
    ↓
③ 三视图生成（调用 Part A 的图片生成 API）
   以裁剪后的主体图为参考图 → image2image 生成三视图
    ↓
④ 结果展示
   3 张图 + 1 张设计总图
```

```
原图                    分割蒙版                  裁剪后的主体              三视图结果
┌──────────┐          ┌──────────┐            ┌──────────┐            ┌────┬────┬────┐
│          │          │██████████│            │          │            │正面│侧面│背面│
│ 人物+背景 │   →      │██░░░░░░██│    →       │  纯主体   │    →      │全身│全身│全身│
│          │          │██░░██░░██│            │  无背景   │            │    │    │    │
│          │          │████░░████│            │          │            └────┴────┴────┘
└──────────┘          └──────────┘            └──────────┘
                      像素级轮廓               干净参考图               一致性大幅提升
```

---

## B3. 自动分割微服务 API

### 部署方式：独立 Docker 微服务

分割服务作为独立容器运行，主后端通过 HTTP 调用，内存完全隔离。

**技术栈：** ONNX Runtime + u2netp 模型

**资源占用：**

| 项目 | 数据 |
|------|------|
| 模型 | u2netp (ONNX, 4.7 MB) |
| 容器内存 | ~150-200 MB（模型 + 推理） |
| CPU 推理速度 | 0.5-1 秒/张（512×512 输入） |
| 真人精度 | ⭐⭐⭐⭐⭐ |
| 动漫精度 | ⭐⭐⭐⭐⭐ |
| GPU | 不需要 |

### B3.1 分割接口

```
POST /segment
```

**Content-Type:** `application/json`

#### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `image` | string | 是 | 原图 base64 data URI，格式 `data:image/xxx;base64,xxxx` |

#### 请求示例

```json
{
  "image": "data:image/png;base64,iVBORw0KGgo..."
}
```

#### 响应

```json
{
  "mask": "data:image/png;base64,iVBORw0KGgo..."
}
```

**mask 说明：** 与原图尺寸一致的黑白 PNG，白色=主体区域，黑色=背景区域。

### B3.2 健康检查

```
GET /health
```

#### 响应

```json
{
  "status": "ok"
}
```

### B3.3 Docker 部署

**Dockerfile：**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY models/ models/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
```

**requirements.txt：**

```
onnxruntime==1.18.1
fastapi==0.111.0
uvicorn==0.30.1
Pillow==10.4.0
numpy==1.26.4
```

**docker-compose.yml：**

```yaml
version: "3.8"

services:
  seg-service:
    build: ./seg-service
    ports:
      - "8001:8001"
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

**主后端调用方式（HTTP）：**

```
主后端 → POST http://seg-service:8001/segment
         请求体: { "image": "data:image/png;base64,xxxx" }
         响应: { "mask": "data:image/png;base64,xxxx" }
```

---

## B4. 三视图生成方案

拿到主体蒙版后，有三种三视图生成方案：

### 方案 A：单次生成三区布局图（推荐首选，一致性最好）

一次生成一张 1920×1080 的角色设计图，包含三视图 + 表情 + 动作：

```
┌──────────┬──────────┬──────────┐
│          │  正面全身 │  表情1   │
│  全身    │  ─────── │  表情2   │
│  立绘    │  侧面全身 │  ─────── │
│          │  ─────── │  动作1   │
│  (35%)   │  背面全身 │  动作2   │
│          │  (35%)   │  (30%)   │
└──────────┴──────────┴──────────┘
      1920 × 1080
```

**调用方式：** `POST /api/images/tasks`

```json
{
  "prompt": "角色「xxx」的完整设计参考图。画面布局严格分三区：左侧全身立绘，中间三视图（正面/侧面/背面），右侧表情和动作特写。所有面板中角色面部、发型、服装必须保持完全一致。纯白背景。",
  "model": "agnes-image-2.1-flash",
  "size": "1920x1080",
  "base64_images": [
    "data:image/png;base64,主体裁剪图..."
  ]
}
```

| 优点 | 缺点 |
|------|------|
| 一致性天然保证（同一张图） | 每个视图尺寸较小 |
| 一次调用，成本最低 | 不方便单独使用某个视图 |

### 方案 B：三次独立生成 + 共享参考图

以裁剪的主体图为参考，分 3 次调用 image2image：

| 生成次序 | prompt | 参考图 | seed |
|---------|--------|--------|------|
| 第 1 次 | `"[主体描述], front view, 正面全身, A-Pose, 白色背景"` | 主体裁剪图 | 固定 |
| 第 2 次 | `"[主体描述], side view, 侧面全身, A-Pose, 白色背景"` | 主体裁剪图 | 固定 |
| 第 3 次 | `"[主体描述], back view, 背面全身, A-Pose, 白色背景"` | 主体裁剪图 | 固定 |

| 优点 | 缺点 |
|------|------|
| 每张图 1024×1024，尺寸大 | 3 次独立生成，一致性靠参数控制 |
| 可单独使用 | 成本是方案 A 的 3 倍 |

### 方案 C：混合方案（推荐最终实现）

先方案 A 生成三区布局图，再用蒙版裁剪功能从布局图中分别裁剪出正面、侧面、背面三张独立图。

| 优点 | 缺点 |
|------|------|
| 既有设计总图又有独立高清图 | 需要额外裁剪步骤 |
| 一致性最好（源自同一张图） | — |
| 完全复用现有 API | — |

---

## B5. 一致性保障手段

无论选择哪种方案，以下手段可进一步提升主体一致性：

| 手段 | 作用 | 实现方式 |
|------|------|---------|
| **干净参考图** | 去掉背景干扰，AI 聚焦主体 | 自动分割 + 裁剪 |
| **相同 seed** | 相同随机种子 → 相似生成路径 | 3 次生成使用相同 seed 值 |
| **negative prompt** | 防止风格漂移/面部变化 | `"deformed, disfigured, face swap, clothing change, hair color change"` |
| **结构化 prompt** | 注入主体外观描述减少歧义 | 在 prompt 中包含发色、服装、体型等描述 |
| **多图参考** | 多张参考图比单张更好 | 第 2、3 次生成时把前一次结果也作为参考图 |

---

# Part C：对接清单

## C1. 蒙版编辑（Part A）需实现

### 前端

- [ ] 图片画布组件：加载原图、画笔涂抹交互
- [ ] 蒙版生成：离屏 canvas 绘制黑白图，导出 base64 data URI
- [ ] 原图 base64 预转换（远程 URL 需下载转 base64）
- [ ] 调用创建任务 API，提交原图 + 蒙版 + 提示词
- [ ] 轮询或 SSE 监听任务状态
- [ ] 任务完成后用结果图片 URL 更新 UI

### 后端

- [ ] 创建任务接口：接收 prompt + 原图 + 蒙版，创建异步任务
- [ ] 查询任务状态接口：返回任务状态和结果
- [ ] 异步任务执行器：调用上游 AI API
- [ ] 蒙版归一化：base64 / data URI / URL 统一为 data URI
- [ ] 上游 API 请求构建：mask 放入 `extra_body.mask`，image 放入 `extra_body.image`

## C2. 自动主体提取（Part B）需实现

### 分割微服务

- [ ] Docker 容器：Python + ONNX Runtime + u2netp 模型
- [ ] `POST /segment` 接口：输入原图 base64 → 输出蒙版 base64
- [ ] `GET /health` 健康检查接口
- [ ] 内存限制 512M，CPU 推理无需 GPU

### 主后端

- [ ] 调用分割微服务获取主体蒙版
- [ ] 用蒙版裁剪原图生成干净主体图
- [ ] 将主体图作为参考图调用 Part A 的图片生成 API
- [ ] 三视图 prompt 模板构建（含 negative prompt、结构化描述）

## C3. 关键约束

- 蒙版图尺寸必须与原图完全一致
- 蒙版必须为纯黑白（白色=编辑/主体，黑色=保留/背景）
- `mask` 和 `image` 必须放在上游请求的 `extra_body` 中，不能放顶层
- 原图和蒙版的 base64 总大小建议不超过 10MB
- 任务为异步模式，前端需实现轮询或 SSE 机制获取结果
