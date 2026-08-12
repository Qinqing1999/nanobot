"""
Debug script: investigate why background removal returns white background
instead of transparent background.

Loads model_dynamic.onnx using ONNX Runtime, runs inference on a test image,
and checks every step of the pipeline for transparency issues.
"""
import os
import sys
import time
import numpy as np
from PIL import Image
import onnxruntime as ort

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "model_dynamic.onnx")
INPUT_SIZE = 640  # minimum usable resolution for RMBG-1.4

# Use nanobot_webui.png as test image (or accept command-line arg)
if len(sys.argv) > 1:
    TEST_IMAGE = sys.argv[1]
else:
    TEST_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "images", "nanobot_webui.png")

print("=" * 70)
print("  RMBG-1.4 Transparency Debug Script")
print("=" * 70)

# Step 0: Check model file
print(f"\n[Step 0] Model file check")
print(f"  Path: {MODEL_PATH}")
print(f"  Exists: {os.path.exists(MODEL_PATH)}")
if os.path.exists(MODEL_PATH):
    print(f"  Size: {os.path.getsize(MODEL_PATH) / 1048576:.1f} MB")

# Step 1: Load model
print(f"\n[Step 1] Loading model with ONNX Runtime...")
so = ort.SessionOptions()
so.enable_cpu_mem_arena = False
so.intra_op_num_threads = 2
sess = ort.InferenceSession(MODEL_PATH, so, providers=["CPUExecutionProvider"])
input_name = sess.get_inputs()[0].name
print(f"  Input name: {input_name}")
print(f"  Input shape: {sess.get_inputs()[0].shape}")
print(f"  Output name: {sess.get_outputs()[0].name}")
print(f"  Output shape: {sess.get_outputs()[0].shape}")
print(f"  Providers: {sess.get_providers()}")

# Step 2: Load test image
print(f"\n[Step 2] Loading test image: {TEST_IMAGE}")
if not os.path.exists(TEST_IMAGE):
    # Try other test images
    for name in ["nanobot_logo.png", "nanobot_arch.png"]:
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "images", name)
        if os.path.exists(alt):
            TEST_IMAGE = alt
            break
    else:
        # Create a synthetic test image
        print("  No test image found, creating synthetic test image...")
        TEST_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_input.png")
        img = Image.new("RGB", (800, 600), (100, 150, 200))
        # Draw a simple "person" shape
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        # Head
        draw.ellipse([350, 100, 450, 200], fill=(255, 220, 180))
        # Body
        draw.rectangle([320, 200, 480, 450], fill=(50, 50, 150))
        # Background pattern
        draw.rectangle([0, 0, 800, 600], fill=(100, 150, 200))
        draw.ellipse([350, 100, 450, 200], fill=(255, 220, 180))
        draw.rectangle([320, 200, 480, 450], fill=(50, 50, 150))
        img.save(TEST_IMAGE)
        print(f"  Saved synthetic test image to: {TEST_IMAGE}")

img = Image.open(TEST_IMAGE)
print(f"  Original size: {img.size[0]}x{img.size[1]}")
print(f"  Original mode: {img.mode}")

# Step 3: Preprocess
print(f"\n[Step 3] Preprocessing (resize to {INPUT_SIZE}x{INPUT_SIZE})...")
if img.mode != "RGB":
    img_rgb = img.convert("RGB")
    print(f"  Converted from {img.mode} to RGB")
else:
    img_rgb = img
ow, oh = img_rgb.size
r = img_rgb.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
a = np.array(r).astype(np.float32) / 255.0
a = (a - 0.5) / 0.5  # normalize to [-1, 1]
a = a.transpose(2, 0, 1)[None, ...]  # HWC -> NCHW
print(f"  Input tensor shape: {a.shape}")
print(f"  Input tensor dtype: {a.dtype}")
print(f"  Input tensor min: {a.min():.4f}, max: {a.max():.4f}")

# Step 4: Run inference
print(f"\n[Step 4] Running inference...")
t0 = time.time()
out = sess.run(None, {input_name: a})
t1 = time.time()
print(f"  Inference time: {(t1 - t0) * 1000:.0f} ms")

raw_output = out[0]
print(f"  Raw output shape: {raw_output.shape}")
print(f"  Raw output dtype: {raw_output.dtype}")
print(f"  Raw output min: {raw_output.min():.6f}")
print(f"  Raw output max: {raw_output.max():.6f}")
print(f"  Raw output mean: {raw_output.mean():.6f}")

# Check if output looks like it's already sigmoid (0-1 range)
if raw_output.min() >= 0 and raw_output.max() <= 1:
    print("  [INFO] Output appears to be already in [0, 1] range (post-sigmoid)")
else:
    print("  [WARNING] Output is NOT in [0, 1] range — may need sigmoid!")

# Step 5: Postprocess — extract mask
print(f"\n[Step 5] Postprocessing (extracting mask)...")
# Extract single channel
if raw_output.ndim == 4:
    m = raw_output[0, 0]
    print(f"  Extracted from 4D output: raw_output[0, 0] -> shape {m.shape}")
elif raw_output.ndim == 3:
    if raw_output.shape[0] == 1:
        m = raw_output[0]
        print(f"  Extracted from 3D output: raw_output[0] -> shape {m.shape}")
    else:
        m = raw_output[0]
        print(f"  Extracted from 3D output (channel 0) -> shape {m.shape}")
else:
    m = raw_output
    print(f"  Using raw output -> shape {m.shape}")

print(f"  Mask (pre-resize) min: {m.min():.6f}, max: {m.max():.6f}, mean: {m.mean():.6f}")

# Resize mask back to original size
mp = Image.fromarray(m.astype(np.float32), mode='F').resize((ow, oh), Image.BILINEAR)
ma = np.array(mp)
print(f"  Mask (post-resize) min: {ma.min():.6f}, max: {ma.max():.6f}, mean: {ma.mean():.6f}")

# Min-max normalize
mx, mn = ma.max(), ma.min()
if mx > mn:
    ma = (ma - mn) / (mx - mn)
print(f"  Mask (normalized) min: {ma.min():.6f}, max: {ma.max():.6f}, mean: {ma.mean():.6f}")

# Convert to uint8
mask_uint8 = (ma * 255).astype(np.uint8)
print(f"  Mask (uint8) min: {mask_uint8.min()}, max: {mask_uint8.max()}, mean: {mask_uint8.mean():.1f}")

# Count foreground vs background
fg_count = (mask_uint8 > 128).sum()
bg_count = (mask_uint8 <= 128).sum()
total = mask_uint8.size
print(f"  Foreground pixels (>128): {fg_count} ({fg_count * 100 / total:.1f}%)")
print(f"  Background pixels (<=128): {bg_count} ({bg_count * 100 / total:.1f}%)")

# Check if mask is all white (degenerate case)
if mask_uint8.min() == 255:
    print("  [CRITICAL ISSUE] Mask is ALL WHITE (255) — this means no background is detected!")
    print("    The model thinks everything is foreground. This is the 512x512 degradation issue.")
    print("    Solution: Use INPUT_SIZE >= 640")
elif mask_uint8.max() == 0:
    print("  [CRITICAL ISSUE] Mask is ALL BLACK (0) — this means no foreground is detected!")
else:
    print("  [OK] Mask has both foreground and background values")

# Step 6: Create RGBA output (transparent background)
print(f"\n[Step 6] Creating RGBA output with transparent background...")
rgba = img.convert("RGBA").copy()
rgba.putalpha(Image.fromarray(mask_uint8, mode='L'))

# Verify the RGBA image
print(f"  RGBA mode: {rgba.mode}")
print(f"  RGBA size: {rgba.size}")
r_arr, g_arr, b_arr, a_arr = rgba.split()
a_np = np.array(a_arr)
print(f"  Alpha channel min: {a_np.min()}, max: {a_np.max()}, mean: {a_np.mean():.1f}")
print(f"  Alpha transparent pixels (0): {(a_np == 0).sum()}")
print(f"  Alpha opaque pixels (255): {(a_np == 255).sum()}")
print(f"  Alpha semi-transparent pixels (1-254): {((a_np > 0) & (a_np < 255)).sum()}")

# Step 7: Save outputs for inspection
output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output")
os.makedirs(output_dir, exist_ok=True)

# Save mask
mask_path = os.path.join(output_dir, "debug_mask.png")
Image.fromarray(mask_uint8, mode='L').save(mask_path)
print(f"\n  Saved mask: {mask_path}")

# Save RGBA transparent result
rgba_path = os.path.join(output_dir, "debug_rgba_transparent.png")
rgba.save(rgba_path)
print(f"  Saved RGBA transparent: {rgba_path}")

# Step 8: ALSO create white-background version for comparison
print(f"\n[Step 7b] Creating white-background version for comparison...")
white_bg = Image.new("RGB", img_rgb.size, (255, 255, 255))
white_result = Image.composite(img_rgb, white_bg, Image.fromarray(mask_uint8, mode='L'))
white_path = os.path.join(output_dir, "debug_white_bg.png")
white_result.save(white_path)
print(f"  Saved white-bg result: {white_path}")

# Step 9: Verify saved PNG actually has alpha channel
print(f"\n[Step 8] Verifying saved PNG files...")
for label, path in [("Mask", mask_path), ("RGBA Transparent", rgba_path), ("White BG", white_path)]:
    verify = Image.open(path)
    print(f"  {label}: mode={verify.mode}, size={verify.size}")
    if verify.mode == "RGBA":
        _, _, _, va = verify.split()
        va_np = np.array(va)
        print(f"    Alpha: min={va_np.min()}, max={va_np.max()}, transparent_px={(va_np == 0).sum()}")

# Step 10: Diagnosis
print(f"\n{'=' * 70}")
print(f"  DIAGNOSIS")
print(f"{'=' * 70}")

if mask_uint8.min() == mask_uint8.max():
    print(f"  PROBLEM: Mask is uniform value {mask_uint8.min()} — model output is degenerate")
    print(f"  ROOT CAUSE: Input resolution {INPUT_SIZE}x{INPUT_SIZE} may be too low")
    print(f"  SOLUTION: Use INPUT_SIZE >= 640 (640 is minimum for RMBG-1.4)")
elif rgba.mode != "RGBA":
    print(f"  PROBLEM: Output image is not RGBA mode")
    print(f"  ROOT CAUSE: Image was not converted to RGBA before putalpha")
    print(f"  SOLUTION: Ensure img.convert('RGBA') before putalpha")
else:
    transparent_count = (a_np == 0).sum()
    if transparent_count == 0:
        print(f"  PROBLEM: No transparent pixels in output")
        print(f"  ROOT CAUSE: Mask has no zero values, or mask is inverted")
        print(f"  SOLUTION: Check mask normalization or invert mask")
    else:
        print(f"  OK: Output has {transparent_count} transparent pixels ({transparent_count * 100 / total:.1f}%)")
        print(f"  The RGBA PNG should have transparent background when viewed in a")
        print(f"  transparency-aware viewer (not Windows Photo Viewer which shows white)")

        print(f"\n  If you still see white background, possible causes:")
        print(f"  1. The image viewer doesn't support transparency (e.g., Windows Photos)")
        print(f"  2. The image was re-encoded as JPEG (which doesn't support alpha)")
        print(f"  3. In the nanobot apply_mask tool, background defaults to 'white'")
        print(f"     -> Pass background='transparent' to get transparent output")
        print(f"  4. The channel sending the image converts to JPEG before sending")

print(f"\n  Output files saved to: {output_dir}/")
print(f"  - debug_mask.png          (grayscale mask)")
print(f"  - debug_rgba_transparent.png  (RGBA with transparent bg)")
print(f"  - debug_white_bg.png      (RGB with white bg, for comparison)")
