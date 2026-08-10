# BiRefNet 作为独立 Docker 微服务而非进程内库

## Status

Superseded by ADR-0010 — the microservice shape (standalone Docker, HTTP API, `providers.birefnet.apiBase`) is retained, but the runtime stack changed from PyTorch BiRefNet to rembg + ONNX Runtime.

## Context

主体分割（Subject Segmentation）需要 BiRefNet ONNX 模型。该模型依赖 `onnxruntime`、`Pillow`、`numpy` 等库，模型文件约 30 MB。nanobot 的核心依赖应保持轻量，不应为单一工具引入重量级 ML 推理依赖。

## Decision

BiRefNet 作为独立 Docker 微服务部署（端口 8001），nanobot 通过 HTTP API 调用。配置通过 `providers.birefnet.apiBase` 指定服务地址。用户自行管理容器生命周期，nanobot 启动时做健康检查并提示。

## Considered Options

- **进程内库**：直接在 nanobot Python 进程中加载 ONNX 模型。否决原因：引入重依赖（onnxruntime ~100 MB+）、模型管理复杂、内存隔离差、影响 nanobot 核心包体积。
- **MCP 服务器**：通过 MCP 协议暴露分割能力。否决原因：MCP 适合 LLM 工具暴露，不适合纯计算服务；增加协议复杂度。
- **独立 Docker 微服务（采纳）**：隔离依赖与内存，HTTP 调用简单，可独立扩展，用户可控启停。

## Consequences

- 用户需自行 `docker compose up` 启动分割服务，增加部署步骤。
- 网络调用引入延迟（本地 HTTP ~5-10 ms，可忽略）。
- nanobot 不依赖 `onnxruntime`，核心包体积不受影响。
- 分割服务可独立升级模型版本而不影响 nanobot。
