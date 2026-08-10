"""Subject segmentation microservice (ONNX Runtime + BiRefNet ONNX).

Provides:
  POST /segment  — input: {"image": "data:image/...;base64,..."} → output: {"mask": "data:image/png;base64,..."}
  GET  /health   — returns {"status": "ok"}

Uses ONNX Runtime with a pre-exported BiRefNet ONNX model.
No PyTorch / transformers / kornia / timm — just onnxruntime.

Prerequisite:
  Run `python export_onnx.py` once on a dev machine to generate
  models/birefnet.onnx, then deploy this Docker image on the server.

For 2-core / 2 GB VPS, consider exporting the lightweight variant
(BiRefNet with swin_v1_tiny backbone) instead of the full model.
"""

from __future__ import annotations

import base64
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
MODEL_PATH = Path(os.environ.get("MODEL_PATH", str(MODEL_DIR / "birefnet.onnx")))
INPUT_SIZE = (1024, 1024)  # must match the size used in export_onnx.py

# ImageNet normalization (must match export_onnx.py)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

app = FastAPI(title="BiRefNet Segmentation Service")

_session: ort.InferenceSession | None = None


def _load_model():
    """Load the ONNX model into an onnxruntime InferenceSession."""
    global _session
    if _session is not None:
        return

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"ONNX model not found: {MODEL_PATH}\n"
            "Run `python export_onnx.py` to generate it first."
        )

    # Configure onnxruntime for CPU inference on a low-core server
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = int(os.environ.get("ORT_THREADS", "0")) or None
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    print(f"Loading ONNX model: {MODEL_PATH} ...")
    _session = ort.InferenceSession(
        str(MODEL_PATH),
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )
    print("Model loaded successfully.")
    print(f"  Input:  {_session.get_inputs()[0].name}  shape={_session.get_inputs()[0].shape}")
    print(f"  Output: {_session.get_outputs()[0].name}  shape={_session.get_outputs()[0].shape}")


@app.on_event("startup")
async def startup():
    _load_model()


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class SegmentRequest(BaseModel):
    image: str


class SegmentResponse(BaseModel):
    mask: str


class HealthResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/segment", response_model=SegmentResponse)
async def segment(req: SegmentRequest):
    """Segment the subject from the input image.  Returns a binary mask."""
    try:
        image = _decode_data_url(req.image)
        original_size = image.size  # (width, height)

        # Preprocess: resize → normalize → (1, 3, H, W) numpy float32
        img_resized = image.resize(INPUT_SIZE, Image.BILINEAR)
        img_array = np.array(img_resized, dtype=np.float32) / 255.0  # (H, W, 3)
        img_array = img_array.transpose(2, 0, 1)  # (3, H, W)
        img_array = (img_array - MEAN) / STD
        img_array = img_array[np.newaxis, ...]  # (1, 3, H, W)

        # Inference
        input_name = _session.get_inputs()[0].name
        outputs = _session.run(None, {input_name: img_array})
        logits = outputs[0]  # (1, 1, H, W) or (1, C, H, W)

        # Post-process: sigmoid → resize to original → threshold
        if logits.ndim == 4:
            logits = logits[0]
        if logits.ndim == 3 and logits.shape[0] == 1:
            logits = logits[0]
        pred = _sigmoid(logits)  # (H, W)

        # Resize mask back to original image dimensions
        mask_image = Image.fromarray((pred * 255).astype(np.uint8), mode="L")
        mask_image = mask_image.resize(original_size, Image.BILINEAR)

        # Binarize: threshold at 0.5 (128/255)
        mask_np = np.array(mask_image, dtype=np.uint8)
        mask_np = (mask_np > 128).astype(np.uint8) * 255
        mask_image = Image.fromarray(mask_np, mode="L")

        return SegmentResponse(mask=_encode_data_url(mask_image))
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_data_url(data_url: str) -> Image.Image:
    """Decode a base64 data URL to a PIL Image."""
    if "," in data_url:
        _, encoded = data_url.split(",", 1)
    else:
        encoded = data_url
    raw = base64.b64decode(encoded)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _encode_data_url(image: Image.Image) -> str:
    """Encode a PIL Image as a base64 PNG data URL."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid for numpy arrays."""
    return np.where(x >= 0, 1 / (1 + np.exp(-x)), np.exp(x) / (1 + np.exp(x)))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
