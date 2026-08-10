"""BiRefNet subject segmentation microservice.

Provides:
  POST /segment  — input: {"image": "data:image/...;base64,..."} → output: {"mask": "data:image/png;base64,..."}
  GET  /health   — returns {"status": "ok"}
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = os.environ.get("BIREFNET_MODEL_ID", "ZhengPeng7/BiRefNet")
DEVICE = "cpu"
INPUT_SIZE = (1024, 1024)  # BiRefNet default input size

# ImageNet normalization
MEAN = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)

app = FastAPI(title="BiRefNet Segmentation Service")

_model = None


def _load_model():
    """Load BiRefNet model from HuggingFace."""
    global _model
    if _model is not None:
        return

    from transformers import AutoModelForImageSegmentation

    print(f"Loading BiRefNet model: {MODEL_ID} on {DEVICE}...")
    _model = AutoModelForImageSegmentation.from_pretrained(
        MODEL_ID,
        trust_remote_code=True,
        torch_dtype=torch.float32,
    )
    _model.to(DEVICE)
    _model.eval()
    print("Model loaded successfully.")


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
    """Segment the subject from the input image."""
    try:
        image = _decode_data_url(req.image)
        original_size = image.size  # (width, height)

        # Preprocess: resize to model input size, normalize
        img_resized = image.resize(INPUT_SIZE, Image.BILINEAR)
        img_tensor = _to_tensor(img_resized)  # (1, 3, H, W)

        # Inference
        with torch.no_grad():
            outputs = _model(img_tensor)

        # Post-process: get prediction and resize back
        pred = outputs.logits if hasattr(outputs, "logits") else outputs[0]
        if isinstance(pred, (list, tuple)):
            pred = pred[0]
        if pred.ndim == 4:
            pred = pred.squeeze(1)
        pred = torch.sigmoid(pred)

        # Resize to original image dimensions (height, width)
        pred = F.interpolate(
            pred.unsqueeze(0) if pred.ndim == 2 else pred.unsqueeze(1),
            size=(original_size[1], original_size[0]),
            mode="bilinear",
            align_corners=False,
        ).squeeze()

        # Binary mask (threshold 0.5)
        mask_np = (pred.cpu().numpy() > 0.5).astype(np.uint8) * 255
        mask_image = Image.fromarray(mask_np, mode="L")

        # Free memory
        del img_tensor, outputs, pred
        torch.cuda.empty_cache() if torch.cuda.is_available() else None

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


def _to_tensor(image: Image.Image) -> torch.Tensor:
    """Convert PIL image to normalized tensor (1, 3, H, W)."""
    arr = np.array(image, dtype=np.float32) / 255.0  # (H, W, 3)
    tensor = torch.from_numpy(arr).permute(2, 0, 1)  # (3, H, W)
    tensor = (tensor - MEAN) / STD
    return tensor.unsqueeze(0).to(DEVICE)  # (1, 3, H, W)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
