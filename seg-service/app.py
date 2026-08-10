"""Subject segmentation microservice (ONNX Runtime + silueta).

Provides:
  POST /segment  — input: {"image": "data:image/...;base64,..."} → output: {"mask": "data:image/png;base64,..."}
  GET  /health   — returns {"status": "ok"}

Uses ONNX Runtime directly with silueta model (~43 MB, 320×320 input).
No rembg / scipy / numba — just onnxruntime. Runtime memory ~200-300 MB.
"""

from __future__ import annotations

import base64
import gc
import io
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
import uvicorn
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "models"))
MODEL_PATH = Path(os.environ.get("MODEL_PATH", str(MODEL_DIR / "silueta.onnx")))
INPUT_SIZE = (320, 320)  # silueta input size
MAX_DIMENSION = int(os.environ.get("MAX_DIMENSION", "512"))  # resize large images to prevent OOM on low-memory servers
MAX_PAYLOAD_BYTES = int(os.environ.get("MAX_PAYLOAD_BYTES", str(8 * 1024 * 1024)))  # reject base64 payloads larger than ~8 MB (≈6 MB image)

# ImageNet normalization
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

app = FastAPI(title="Subject Segmentation Service")

_session: ort.InferenceSession | None = None


def _load_model():
    global _session
    if _session is not None:
        return
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"ONNX model not found: {MODEL_PATH}")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = int(os.environ.get("ORT_THREADS", "1")) or None
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    # Use basic optimization to reduce memory footprint
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    print(f"Loading ONNX model: {MODEL_PATH} ...")
    _session = ort.InferenceSession(
        str(MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"],
    )
    print("Model loaded successfully.")
    print(f"  Input:  {_session.get_inputs()[0].name}  shape={_session.get_inputs()[0].shape}")
    print(f"  Output: {_session.get_outputs()[0].name}  shape={_session.get_outputs()[0].shape}")


@app.on_event("startup")
async def startup():
    _load_model()


class SegmentRequest(BaseModel):
    image: str

class SegmentResponse(BaseModel):
    mask: str

class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/segment", response_model=SegmentResponse)
async def segment(req: SegmentRequest):
    """Segment the subject from the input image. Returns a binary mask."""
    try:
        # Early payload size check — reject before decoding to save memory
        if len(req.image) > MAX_PAYLOAD_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=413, content={
                "error": f"Image payload too large ({len(req.image) // 1024} KB). "
                         f"Limit is {MAX_PAYLOAD_BYTES // 1024} KB."
            })

        image = _decode_data_url(req.image)
        original_size = image.size

        # Safety: resize large images to prevent OOM on low-memory servers
        # Use thumbnail() for the initial downscale — it modifies in-place,
        # using far less peak memory than resize() which creates a new copy.
        max_dim = max(original_size)
        if max_dim > MAX_DIMENSION:
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.BILINEAR)
            original_size = image.size

        # Preprocess: resize → normalize → (1, 3, H, W)
        img_resized = image.resize(INPUT_SIZE, Image.BILINEAR)
        img_array = np.array(img_resized, dtype=np.float32) / 255.0
        img_array = img_array.transpose(2, 0, 1)
        img_array = (img_array - MEAN) / STD
        img_array = img_array[np.newaxis, ...]

        # Free intermediate image objects before inference
        del img_resized

        # Inference
        input_name = _session.get_inputs()[0].name
        outputs = _session.run(None, {input_name: img_array})
        pred = outputs[0]

        # Free the input array and raw outputs immediately
        del img_array, outputs
        gc.collect()

        # Post-process: normalize to [0,1] → resize → threshold
        if pred.ndim == 4:
            pred = pred[0]
        if pred.ndim == 3 and pred.shape[0] == 1:
            pred = pred[0]
        # Min-max normalize (rembg style)
        pred = (pred - pred.min()) / (pred.max() - pred.min() + 1e-8)

        mask_image = Image.fromarray((pred * 255).astype(np.uint8), mode="L")
        del pred
        mask_image = mask_image.resize(original_size, Image.BILINEAR)
        mask_np = np.array(mask_image, dtype=np.uint8)
        mask_np = (mask_np > 128).astype(np.uint8) * 255
        mask_image = Image.fromarray(mask_np, mode="L")

        result = SegmentResponse(mask=_encode_data_url(mask_image))
        del mask_image, mask_np
        gc.collect()
        return result
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


def _decode_data_url(data_url: str) -> Image.Image:
    if "," in data_url:
        _, encoded = data_url.split(",", 1)
    else:
        encoded = data_url
    raw = base64.b64decode(encoded)
    img = Image.open(io.BytesIO(raw))
    # For JPEG, use draft mode to reduce decoded size early (saves memory)
    if img.format == "JPEG" and max(img.size) > MAX_DIMENSION * 2:
        target = (MAX_DIMENSION * 2, MAX_DIMENSION * 2)
        img.draft("RGB", target)
    img = img.convert("RGB")
    del raw
    return img


def _encode_data_url(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
