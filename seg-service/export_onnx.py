"""One-time script: export BiRefNet PyTorch model → ONNX.

Run this on a dev machine that has PyTorch + transformers installed.
The resulting .onnx file is then deployed in Docker with onnxruntime only —
no torch / transformers / kornia / timm needed at runtime.

Usage:
  pip install torch torchvision transformers einops kornia timm onnx onnxsim
  python export_onnx.py                          # full BiRefNet (ZhengPeng7/BiRefNet)
  python export_onnx.py --model ZhengPeng7/BiRefNet --output models/birefnet.onnx

For the lightweight variant (swin_v1_tiny backbone, x4 faster / x5 smaller),
download the tiny checkpoint from the BiRefNet GitHub releases and point --model
to the local directory containing the weights + config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

# ImageNet normalization (must match app.py preprocessing)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
INPUT_SIZE = (1024, 1024)  # BiRefNet default input size


def export(model_id: str, output_path: str, opset: int = 17):
    """Load BiRefNet from HuggingFace and export to ONNX."""
    from transformers import AutoModelForImageSegmentation

    print(f"Loading model: {model_id} ...")
    model = AutoModelForImageSegmentation.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype=torch.float32,
    )
    model.to("cpu")
    model.eval()

    # Dummy input for tracing
    dummy = torch.randn(1, 3, *INPUT_SIZE, dtype=torch.float32)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting to ONNX (opset {opset}) → {output_file} ...")
    torch.onnx.export(
        model,
        dummy,
        str(output_file),
        export_params=True,
        opset_version=opset,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={
            # Allow variable batch size (rarely needed, but harmless)
            "input": {0: "batch"},
            "logits": {0: "batch"},
        },
    )
    print(f"ONNX export complete: {output_file} ({output_file.stat().st_size / 1e6:.1f} MB)")

    # Optional: simplify the graph to reduce file size and improve inference speed
    try:
        import onnx
        from onnxsim import simplify

        print("Simplifying ONNX graph ...")
        onnx_model = onnx.load(str(output_file))
        simplified, check = simplify(onnx_model)
        if check:
            onnx.save(simplified, str(output_file))
            print(f"Simplified: {output_file} ({output_file.stat().st_size / 1e6:.1f} MB)")
        else:
            print("Simplification check failed — keeping original graph.")
    except ImportError:
        print("onnx-simplifier not installed — skipping simplification (pip install onnxsim to enable).")

    # Quick sanity check: load with onnxruntime and run one inference
    try:
        import onnxruntime as ort

        print("Sanity check with onnxruntime ...")
        sess = ort.InferenceSession(str(output_file), providers=["CPUExecutionProvider"])
        result = sess.run(None, {"input": dummy.numpy()})
        logits = result[0]
        print(f"  Output shape: {logits.shape}, dtype: {logits.dtype}")
        print(f"  Output range: [{logits.min():.4f}, {logits.max():.4f}]")
        print("✓ ONNX model works correctly with onnxruntime.")
    except ImportError:
        print("onnxruntime not installed — skipping sanity check.")

    print(f"\nDone. Copy {output_file} into seg-service/models/ for Docker deployment.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export BiRefNet to ONNX")
    parser.add_argument(
        "--model",
        default="ZhengPeng7/BiRefNet",
        help="HuggingFace model ID or local path (default: ZhengPeng7/BiRefNet)",
    )
    parser.add_argument(
        "--output",
        default="models/birefnet.onnx",
        help="Output ONNX file path (default: models/birefnet.onnx)",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX opset version (default: 17)",
    )
    args = parser.parse_args()
    export(args.model, args.output, args.opset)
