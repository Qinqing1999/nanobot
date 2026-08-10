"""Test segmentation with a real image. Saves original, mask, and cutout."""
import base64, io, json, sys, urllib.request
from PIL import Image

# Use a real image from the project
image_path = sys.argv[1] if len(sys.argv) > 1 else "/home/nanobot/agnes-cid1.png"
print(f"Loading image: {image_path}")
img = Image.open(image_path).convert("RGB")
print(f"  Original size: {img.size}")

# Encode as data URL
buf = io.BytesIO()
img.save(buf, format="PNG")
data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

# Send to /segment
print("Sending to /segment ...")
req = urllib.request.Request(
    "http://localhost:8001/segment",
    data=json.dumps({"image": data_url}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=120)
result = json.loads(resp.read())
mask_url = result.get("mask", "")

# Decode mask
_, encoded = mask_url.split(",", 1)
mask_bytes = base64.b64decode(encoded)
mask_img = Image.open(io.BytesIO(mask_bytes)).convert("L")
print(f"  Mask size: {mask_img.size}")
print(f"  Mask mode: {mask_img.mode}")

# Count foreground vs background pixels
pixels = list(mask_img.getdata())
fg = sum(1 for p in pixels if p > 128)
bg = sum(1 for p in pixels if p <= 128)
total = len(pixels)
print(f"  Foreground: {fg} pixels ({fg*100//total}%)")
print(f"  Background: {bg} pixels ({bg*100//total}%)")

# Create cutout (original with transparency from mask)
cutout = img.copy()
cutout.putalpha(mask_img.resize(cutout.size, Image.BILINEAR))

# Save results
out_dir = "/tmp/seg_result"
import os; os.makedirs(out_dir, exist_ok=True)
img.save(f"{out_dir}/original.png")
mask_img.save(f"{out_dir}/mask.png")
cutout.save(f"{out_dir}/cutout.png")

# Also save a side-by-side comparison
w, h = img.size
combined = Image.new("RGB", (w * 3 + 20, h), (128, 128, 128))
combined.paste(img, (0, 0))
combined.paste(mask_img.convert("RGB"), (w + 10, 0))
combined.paste(cutout.convert("RGB"), (w * 2 + 20, 0))
combined.save(f"{out_dir}/comparison.png")

print(f"\nResults saved to {out_dir}/")
print(f"  original.png  - input image")
print(f"  mask.png      - segmentation mask")
print(f"  cutout.png    - foreground cutout (transparent bg)")
print(f"  comparison.png - side-by-side comparison")
print("\n✅ Segmentation test passed!")
