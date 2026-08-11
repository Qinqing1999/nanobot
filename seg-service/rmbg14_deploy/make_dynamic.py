"""
将 1024 原版模型改为动态输入尺寸（支持任意分辨率），
然后用 512x512 推理验证效果。
"""
import onnx
from onnx import shape_inference, helper
import numpy as np
from PIL import Image
import onnxruntime as ort
import os, copy

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
SRC = os.path.join(MODEL_DIR, "model.onnx")
DST = os.path.join(MODEL_DIR, "model_dynamic.onnx")

print(f"加载原版模型: {SRC}")
m = onnx.load(SRC)
g = m.graph

# 1. 修改输入 shape: [1, 3, 1024, 1024] -> [1, 3, 'H', 'W']
for inp in g.input:
    dims = inp.type.tensor_type.shape.dim
    if len(dims) == 4:
        # batch 保持 1, channel 保持 3, H/W 改为动态
        dims[2].Clear()
        dims[2].dim_param = "H"
        dims[3].Clear()
        dims[3].dim_param = "W"
        print(f"  输入 {inp.name}: [1, 3, 1024, 1024] -> [1, 3, 'H', 'W']")

# 2. 修改输出 shape: 改为动态
for out in g.output:
    dims = out.type.tensor_type.shape.dim
    if len(dims) == 4:
        dims[2].Clear()
        dims[2].dim_param = "H_out"
        dims[3].Clear()
        dims[3].dim_param = "W_out"
        print(f"  输出 {out.name}: -> ['batch', 'ch', 'H_out', 'W_out']")

# 3. 清除所有 value_info 中的固定维度标注 (改为 -1 / 动态)
#    这样 ONNX Runtime 不会用旧的 shape 信息做错误优化
changed_vi = 0
for vi in g.value_info:
    dims = vi.type.tensor_type.shape.dim
    changed = False
    for d in dims:
        if d.HasField("dim_value") and d.dim_value > 1:
            # 检查是否是空间维度 (dim_value > 1 且不是 channel)
            d.Clear()
            d.dim_param = ""
            changed = True
    if changed:
        changed_vi += 1
print(f"  清除了 {changed_vi} 个 value_info 的固定空间维度")

# 4. 运行 shape inference 重新推导
print("运行 shape inference...")
m = shape_inference.infer_shapes(m)

# 5. 保存
onnx.save(m, DST)
print(f"保存动态模型: {DST} ({os.path.getsize(DST)/1048576:.1f} MB)")

# 6. 验证: 用 512x512 和 1024x1024 分别推理
print("\n" + "=" * 60)
print("验证动态模型")
print("=" * 60)

import sys
img_path = sys.argv[1] if len(sys.argv) > 1 else None
if not img_path:
    print("用法: python make_dynamic.py [test_image.jpg]")
    print("(不提供测试图片则跳过验证)")
    sys.exit(0)
img = Image.open(img_path)
if img.mode != "RGB":
    img = img.convert("RGB")
print(f"原图: {img.size}")

sess = ort.InferenceSession(DST, providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0]
print(f"模型输入: {inp.name} shape={inp.shape}")

for sz in [512, 1024]:
    print(f"\n--- {sz}x{sz} ---")
    r = img.resize((sz, sz), Image.BILINEAR)
    a = np.array(r).astype(np.float32) / 255.0
    a = (a - 0.5) / 0.5
    a = a.transpose(2, 0, 1)[None, ...]

    out = sess.run(None, {inp.name: a})
    o = out[0]
    print(f"  output: shape={o.shape} min={o.min():.6f} max={o.max():.6f} mean={o.mean():.6f}")

    # 后处理
    m2 = o[0, 0]
    mp = Image.fromarray(m2.astype(np.float32), mode='F').resize(img.size, Image.BILINEAR)
    ma = np.array(mp)
    mx, mn = ma.max(), ma.min()
    if mx > mn:
        ma = (ma - mn) / (mx - mn)
    mask = (ma * 255).astype(np.uint8)
    print(f"  mask: min={mask.min()} max={mask.max()} mean={mask.mean():.1f}")
    h, _ = np.histogram(mask, bins=5)
    fg_pct = h[-1] / mask.size * 100
    print(f"  histogram: {h.tolist()}")
    print(f"  foreground: {fg_pct:.1f}%")

    # 保存
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"dynamic_mask_{sz}.png")
    Image.fromarray(mask, mode='L').save(out_path)
    rgba = img.convert("RGBA").copy()
    rgba.putalpha(Image.fromarray(mask, mode='L'))
    rgba_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"dynamic_result_{sz}.png")
    rgba.save(rgba_path)
    print(f"  saved: {out_path}")
    print(f"  saved: {rgba_path}")
