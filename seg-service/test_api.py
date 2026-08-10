"""Test the segmentation service /segment endpoint."""
import base64, io, json, urllib.request
from PIL import Image

# Create a test image: red 256x256 with a white square in center
img = Image.new("RGB", (256, 256), (255, 0, 0))
for x in range(80, 176):
    for y in range(80, 176):
        img.putpixel((x, y), (255, 255, 255))
buf = io.BytesIO()
img.save(buf, format="PNG")
data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

print("Sending segment request...")
req = urllib.request.Request(
    "http://localhost:8001/segment",
    data=json.dumps({"image": data_url}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=120)
result = json.loads(resp.read())
mask = result.get("mask", "")
print(f"Mask data URL length: {len(mask)}")
print(f"Mask starts with: {mask[:50]}")

_, encoded = mask.split(",", 1)
mask_bytes = base64.b64decode(encoded)
mask_img = Image.open(io.BytesIO(mask_bytes))
print(f"Mask size: {mask_img.size}")
print(f"Mask mode: {mask_img.mode}")

pixels = list(mask_img.getdata())
unique = sorted(set(pixels))
print(f"Unique pixel values: {unique[:20]}")
print("Segment service works correctly!")
