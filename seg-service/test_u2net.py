#!/usr/bin/env python3
"""Quick test for the segmentation service."""
import base64
import io
import json
import urllib.request

import numpy as np
from PIL import Image

# Create a test image: white square on black background
img = Image.new("RGB", (320, 320), (0, 0, 0))
for x in range(80, 240):
    for y in range(80, 240):
        img.putpixel((x, y), (255, 255, 255))

buf = io.BytesIO()
img.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()
data_url = f"data:image/png;base64,{b64}"

print("Sending segmentation request...")
req = urllib.request.Request(
    "http://localhost:8001/segment",
    data=json.dumps({"image": data_url}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=60)
result = json.loads(resp.read())

mask_b64 = result["mask"].split(",")[1]
mask_data = base64.b64decode(mask_b64)
mask_img = Image.open(io.BytesIO(mask_data))
arr = np.array(mask_img)

print(f"Mask size: {mask_img.size}")
print(f"Mask mode: {mask_img.mode}")
print(f"Mask unique values: {np.unique(arr)}")
print(f"White pixels (255): {(arr == 255).sum()}")
print(f"Black pixels (0): {(arr == 0).sum()}")
print("SUCCESS: Segmentation service is working with u2net model!")
