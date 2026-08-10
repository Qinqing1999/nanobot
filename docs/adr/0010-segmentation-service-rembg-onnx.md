# 分割服务从 PyTorch BiRefNet 迁移到 ONNX Runtime

## Status

Accepted — supersedes ADR-0008 in implementation detail (the microservice shape is unchanged, but the runtime stack changes).

## Context

ADR-0008 decided to deploy subject segmentation as a standalone Docker microservice.  The original implementation used PyTorch + `transformers.AutoModelForImageSegmentation` to load the full BiRefNet model (`ZhengPeng7/BiRefNet`), pulling in `torch` (~800 MB), `torchvision`, `transformers`, `einops`, `kornia`, and `timm` as dependencies.  The resulting Docker image was ~2 GB+ and runtime memory exceeded 1 GB, making it impractical on a 2-core / 2 GB VPS.

The ADR-0008 text already mentioned "ONNX" as the intended format, but the code never performed the ONNX conversion — it remained a PyTorch implementation.

A proposal was made to introduce a classifier + dual models (MODNet for portraits, "BiRefNet-lite" for anime).  Grilling revealed:

- "BiRefNet-lite" is not an official model name (the lightweight variant is "BiRefNet with swin_v1_tiny backbone").
- MODNet is a portrait-only model (~25 MB ONNX), not the claimed 1.2 MB; it cannot handle products, animals, or other non-human subjects.
- A classifier adds complexity and failure paths (mixed content, cosplay, 3D renders) without clear benefit.
- The real weight source was the PyTorch runtime, not the model file.

## Decision

Replace the PyTorch-based implementation with **ONNX Runtime** only.  The service offers two interchangeable backends, selectable by which model file is placed in `models/`:

### Option A (adopted as default): BiRefNet ONNX

Run `python export_onnx.py` once on a dev machine to export BiRefNet to ONNX, then deploy the `.onnx` file in Docker with `onnxruntime` — no torch/transformers at runtime.  For 2-core / 2 GB servers, use the lightweight variant (swin_v1_tiny backbone) for x4 speed / x5 size improvement.

- Docker image: ~200-300 MB (onnxruntime + fastapi + Pillow)
- Runtime memory: ~200-400 MB
- Model file: ~30-100 MB depending on backbone and quantization
- Quality: SOTA, identical to the original PyTorch BiRefNet

### Option B (lighter alternative): rembg pre-built ONNX models

`pip install rembg[cpu]` — rembg uses onnxruntime internally with pre-built ONNX models (ISNet, U2Net, etc.).  No export step needed.

- Docker image: ~200-300 MB
- Runtime memory: ~200-400 MB
- Model file: auto-downloaded by rembg (4.5-176 MB depending on model)
- Quality: slightly below BiRefNet for fine edges (hair, fur), but adequate for most subjects

The API contract (`POST /segment`, `GET /health`) is unchanged.  The nanobot-side config has moved from `providers.birefnet.apiBase` to `tools.image_generation.segmentation_api_base` — the setting now appears in the WebUI Image settings page under "Subject segmentation" instead of the Providers list.

## Considered Options

- **Dual-model + classifier (MODNet + BiRefNet-lite)**: rejected — adds unnecessary complexity, narrows scope from "any subject" to "person vs anime", relies on non-existent model names, and the classifier itself is a new failure path.
- **BiRefNet ONNX (adopted)**: same model quality as the original, but zero PyTorch dependency at runtime.  Requires a one-time `torch.onnx.export()` on a dev machine.  The `export_onnx.py` script automates this, including graph simplification and sanity check.
- **rembg + ONNX Runtime (alternative)**: minimal dependencies, pre-built ONNX models, no export step.  Slightly lower edge quality than BiRefNet.

## Consequences

- Docker image shrinks from ~2 GB to ~200-300 MB.
- Runtime memory drops from ~1 GB+ to ~200-400 MB — fits comfortably in a 2 GB VPS.
- Option A (BiRefNet ONNX) requires a one-time export step (`python export_onnx.py`) on a machine with PyTorch.  The export script is committed in `birefnet-service/export_onnx.py` but the `.onnx` model file is `.gitignore`d (too large for git).
- Option B (rembg) is zero-step but offers slightly lower edge quality.
- The `providers.birefnet` config key has been removed; the segmentation service URL is now configured via `tools.image_generation.segmentation_api_base` in the WebUI Image settings page.  Existing users who had `providers.birefnet.apiBase` set should migrate to the new key.
- The CONTEXT.md glossary has been updated to reflect that the service is no longer "BiRefNet" specifically.
