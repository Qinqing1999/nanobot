"""Subject segmentation microservice (ONNX Runtime).

Supports two model types:
  - silueta (~43 MB, 320×320 input, ImageNet normalization)
  - rmbg14  (~168 MB, 640×640+ input, [-0.5, 0.5] normalization)

Model type is auto-detected from MODEL_PATH filename, or set via MODEL_TYPE env var.

Provides:
  POST /segment   — input: {"image": "data:image/...;base64,..."} → output: {"mask": "data:image/png;base64,..."}
  POST /remove_bg — input: {"image": "data:image/...;base64,..."} → output: {"result": "data:image/png;base64,..."} (transparent PNG)
  GET  /health    — returns {"status": "ok"}

No rembg / scipy / numba — just onnxruntime. Runtime memory ~200-300 MB (silueta) / ~400 MB (rmbg14).
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
MAX_DIMENSION = int(os.environ.get("MAX_DIMENSION", "512"))  # resize large images to prevent OOM on low-memory servers
MAX_PAYLOAD_BYTES = int(os.environ.get("MAX_PAYLOAD_BYTES", str(8 * 1024 * 1024)))  # reject base64 payloads larger than ~8 MB (≈6 MB image)

# ---------------------------------------------------------------------------
# Model type detection
# ---------------------------------------------------------------------------

def _detect_model_type(model_path: Path) -> str:
    """Auto-detect model type from filename, or use MODEL_TYPE env var."""
    forced = os.environ.get("MODEL_TYPE", "").strip().lower()
    if forced in ("silueta", "rmbg14", "rmbg"):
        return "rmbg14" if forced in ("rmbg14", "rmbg") else "silueta"

    name = model_path.name.lower()
    if "model_dynamic" in name or "rmbg" in name or "model.onnx" == name:
        return "rmbg14"
    return "silueta"


MODEL_TYPE = _detect_model_type(MODEL_PATH)

# Model-specific configuration
if MODEL_TYPE == "rmbg14":
    # RMBG-1.4: trained at 1024x1024, minimum usable is 640x640
    INPUT_SIZE = (640, 640)
    # RMBG-1.4 uses simple [-0.5, 0.5] normalization (i.e., (x/255 - 0.5) / 0.5)
    MEAN = np.array([0.5, 0.5, 0.5], dtype=np.float32).reshape(3, 1, 1)
    STD = np.array([0.5, 0.5, 0.5], dtype=np.float32).reshape(3, 1, 1)
    # RMBG-1.4: output is already post-sigmoid [0,1], use min-max normalize without thresholding
    THRESHOLD_MASK = False
else:
    # silueta: 320x320 input, ImageNet normalization
    INPUT_SIZE = (320, 320)
    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
    # silueta: threshold at 128 for binary mask
    THRESHOLD_MASK = True

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
    # Disable memory arena for RMBG-1.4 (large model, saves ~40% peak memory)
    if MODEL_TYPE == "rmbg14":
        opts.enable_cpu_mem_arena = False
    print(f"Loading ONNX model: {MODEL_PATH} (type={MODEL_TYPE}) ...")
    _session = ort.InferenceSession(
        str(MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"],
    )
    print("Model loaded successfully.")
    print(f"  Input:  {_session.get_inputs()[0].name}  shape={_session.get_inputs()[0].shape}")
    print(f"  Output: {_session.get_outputs()[0].name}  shape={_session.get_outputs()[0].shape}")
    print(f"  Input size: {INPUT_SIZE[0]}x{INPUT_SIZE[1]}")
    print(f"  Threshold mask: {THRESHOLD_MASK}")


@app.on_event("startup")
async def startup():
    _load_model()


class SegmentRequest(BaseModel):
    image: str

class SegmentResponse(BaseModel):
    mask: str

class RemoveBgRequest(BaseModel):
    image: str

class RemoveBgResponse(BaseModel):
    result: str

class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


def _run_inference(image: Image.Image) -> tuple[np.ndarray, tuple[int, int]]:
    """Run model inference and return (mask_uint8, original_size).

    The mask is a grayscale uint8 array with values 0-255.
    """
    original_size = image.size

    # Safety: resize large images to prevent OOM on low-memory servers
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

    del img_resized

    # Inference
    input_name = _session.get_inputs()[0].name
    outputs = _session.run(None, {input_name: img_array})
    pred = outputs[0]

    del img_array, outputs
    gc.collect()

    # Post-process: extract single channel
    if pred.ndim == 4:
        pred = pred[0]
    if pred.ndim == 3 and pred.shape[0] == 1:
        pred = pred[0]

    # Min-max normalize to [0, 1]
    pred = (pred - pred.min()) / (pred.max() - pred.min() + 1e-8)

    # Resize mask back to original size (as float for smooth resampling)
    mask_image = Image.fromarray((pred * 255).astype(np.uint8), mode="L")
    del pred
    mask_image = mask_image.resize(original_size, Image.BILINEAR)
    mask_np = np.array(mask_image, dtype=np.uint8)

    if THRESHOLD_MASK:
        # silueta: binary threshold at 128
        mask_np = (mask_np > 128).astype(np.uint8) * 255

    del mask_image
    return mask_np, original_size


@app.post("/segment", response_model=SegmentResponse)
async def segment(req: SegmentRequest):
    """Segment the subject from the input image. Returns a binary/grayscale mask."""
    try:
        # Early payload size check — reject before decoding to save memory
        if len(req.image) > MAX_PAYLOAD_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=413, content={
                "error": f"Image payload too large ({len(req.image) // 1024} KB). "
                         f"Limit is {MAX_PAYLOAD_BYTES // 1024} KB."
            })

        image = _decode_data_url(req.image)
        mask_np, _ = _run_inference(image)
        mask_image = Image.fromarray(mask_np, mode="L")

        result = SegmentResponse(mask=_encode_data_url(mask_image))
        del mask_image, mask_np
        gc.collect()
        return result
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/remove_bg", response_model=RemoveBgResponse)
async def remove_bg(req: RemoveBgRequest):
    """Remove background from the input image. Returns a transparent PNG (RGBA)."""
    try:
        # Early payload size check
        if len(req.image) > MAX_PAYLOAD_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=413, content={
                "error": f"Image payload too large ({len(req.image) // 1024} KB). "
                         f"Limit is {MAX_PAYLOAD_BYTES // 1024} KB."
            })

        image = _decode_data_url(req.image)
        mask_np, _ = _run_inference(image)

        # Create RGBA image with mask as alpha channel
        # This produces a transparent background PNG
        rgba = image.convert("RGBA")
        mask_image = Image.fromarray(mask_np, mode="L")
        # Ensure mask matches image size (in case of thumbnail resize)
        if mask_image.size != rgba.size:
            mask_image = mask_image.resize(rgba.size, Image.BILINEAR)
        rgba.putalpha(mask_image)

        result = RemoveBgResponse(result=_encode_data_url(rgba))
        del rgba, mask_image, mask_np
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
