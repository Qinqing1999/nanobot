# Subject Segmentation Service

轻量级图片主体分割微服务,基于 ONNX Runtime + u2netp 模型(4.7 MB),适合低内存服务器部署。

## 架构概览

| 项目 | 规格 |
|------|------|
| 模型 | u2netp (ONNX, 4.7 MB) |
| 输入尺寸 | 320×320 |
| 推理后端 | ONNX Runtime (CPUExecutionProvider) |
| 运行时内存 | ~100 MB 空闲, ~300 MB 推理峰值 |
| 容器内存限制 | 512 MB (硬限制,防止 OOM 拖垮宿主机) |
| 框架 | FastAPI + Uvicorn |
| 端口 | 8001 |

## API 文档

### `GET /health`

健康检查端点。

**响应**:
```json
{"status": "ok"}
```

**示例**:
```bash
curl http://localhost:8001/health
```

---

### `POST /segment`

对输入图片进行主体分割,返回二值蒙版。

**请求体**:
```json
{
  "image": "data:image/png;base64,<base64编码的图片数据>"
}
```

- `image`: Data URL 格式的图片。支持 PNG / JPEG / WEBP 等 Pillow 可读格式。

**成功响应** (200):
```json
{
  "mask": "data:image/png;base64,<base64编码的蒙版数据>"
}
```

- `mask`: Data URL 格式的黑白蒙版 PNG。白色(255)= 主体区域,黑色(0)= 背景。

**错误响应** (500):
```json
{
  "error": "错误描述"
}
```

**示例**:
```bash
# 将图片编码为 base64 data URL 并调用
IMAGE_B64=$(base64 -w 0 my_image.png)
curl -X POST http://localhost:8001/segment \
  -H "Content-Type: application/json" \
  -d "{\"image\": \"data:image/png;base64,${IMAGE_B64}\"}"
```

**Python 示例**:
```python
import base64, json, urllib.request

with open("my_image.png", "rb") as f:
    data_url = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"

req = urllib.request.Request(
    "http://localhost:8001/segment",
    data=json.dumps({"image": data_url}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=120)
result = json.loads(resp.read())
mask_url = result["mask"]  # data:image/png;base64,...
```

### 大图保护机制

当输入图片最大边超过 `MAX_DIMENSION`(默认 1024px)时,服务会自动等比缩小后再推理,防止低内存服务器 OOM。返回的蒙版尺寸对应缩小后的图片尺寸。

## Docker 部署

### 方式一:独立构建运行

```bash
# 构建
docker build -t seg-service:latest seg-service/

# 运行(带 512MB 内存限制)
docker run -d \
  --name seg-service \
  -p 8001:8001 \
  --restart unless-stopped \
  --memory=512m \
  --memory-swap=512m \
  seg-service:latest
```

### 方式二:docker compose(独立)

```bash
cd seg-service
docker compose up -d
```

`docker-compose.yml` 配置:
```yaml
services:
  seg-service:
    build: .
    image: seg-service:latest
    container_name: seg-service
    ports:
      - "8001:8001"
    environment:
      - ORT_THREADS=2
    restart: unless-stopped
    mem_limit: 512m
    memswap_limit: 512m
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"]
      interval: 30s
      timeout: 10s
      start_period: 30s
```

### 方式三:根目录 docker-compose.yml(与 nanobot 一起部署)

在项目根目录:
```bash
docker compose up -d seg-service
```

根 `docker-compose.yml` 中的 seg-service 服务定义了同样的内存限制和健康检查。

### 内存限制说明

在 1.6 GB RAM 的低内存服务器上,**必须**设置容器内存限制。否则推理时内存飙升会触发系统级 OOM,导致服务器崩溃重启。当前配置:

| 参数 | 值 | 作用 |
|------|-----|------|
| `mem_limit` | 512m | 容器可用内存上限 |
| `memswap_limit` | 512m | 不允许使用 swap(设为与 mem_limit 相同) |

当容器超出内存限制时,Docker 会 kill 容器进程(而非宿主机进程),容器随后自动重启。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODEL_PATH` | `models/u2netp.onnx` | ONNX 模型文件路径 |
| `MODEL_DIR` | `models` | 模型目录(当 MODEL_PATH 未设置时使用) |
| `ORT_THREADS` | `2` | ONNX Runtime 推理线程数 |
| `PORT` | `8001` | 服务监听端口 |
| `MAX_DIMENSION` | `1024` | 输入图片最大边,超过则自动缩小 |

## 文件结构

```
seg-service/
├── app.py              # FastAPI 应用(主入口)
├── Dockerfile          # 容器构建文件
├── docker-compose.yml  # 独立部署配置
├── requirements.txt    # Python 依赖
├── .dockerignore       # Docker 构建排除项
├── models/
│   └── u2netp.onnx     # 预下载的 ONNX 模型 (4.7 MB)
├── test_api.py         # API 基础测试(合成图片)
└── test_image.py       # 真实图片分割测试
```

## nanobot 集成

nanobot 通过 `segment_subject` 工具调用此服务。工具链流程:

```
用户上传图片 → segment_subject (调用 /segment) → 返回蒙版文件路径
                                                     ↓
                                              apply_mask (本地 PIL 合成) → 干净主体图
```

### 配置方式

在 `~/.nanobot/config.json` 中设置:

```json
{
  "tools": {
    "image_generation": {
      "enabled": true,
      "segmentation_api_base": "http://localhost:8001"
    }
  }
}
```

或通过 WebUI 设置页面 → Image Generation → Segmentation service 填写 URL。

当 `segmentation_api_base` 为空时,`segment_subject` 工具不会注册到 Agent,`apply_mask` 工具始终可用(纯本地 Pillow 处理)。

### 相关源码

| 文件 | 说明 |
|------|------|
| `nanobot/agent/tools/segment_subject.py` | 分割工具:调用 /segment,保存蒙版 |
| `nanobot/agent/tools/apply_mask.py` | 蒙版应用工具:PIL 合成干净主体图 |
| `nanobot/agent/tools/image_generation.py` | 图片生成工具(含 segmentation_api_base 配置) |
| `nanobot/agent/tools/image_utils.py` | 图片路径/制品 ID 解析共享工具 |

### 工具调用超时

| 操作 | 超时 |
|------|------|
| `/segment` 请求 | 60 秒 |
| `/health` 检查 | 5 秒 |

## 测试

```bash
# 基础 API 测试(使用合成图片)
python seg-service/test_api.py

# 真实图片测试
python seg-service/test_image.py /path/to/image.png
```

测试脚本会在 `/tmp/seg_result/` 下保存原图、蒙版和裁剪结果用于对比。
