"""精确测量 enable_cpu_mem_arena=False 的真实推理峰值内存"""
import os, gc, time, threading
import numpy as np
from PIL import Image
import onnxruntime as ort
import psutil

import sys
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "model.onnx")
IMG_PATH = sys.argv[1] if len(sys.argv) > 1 else None

proc = psutil.Process()
if not IMG_PATH:
    print("用法: python bench_peak_precise.py <test_image.jpg>")
    sys.exit(1)

def mb():
    return proc.memory_info().rss / 1048576

# 内存监控线程
peak_mem = [0]
monitoring = [True]

def monitor():
    while monitoring[0]:
        m = mb()
        if m > peak_mem[0]:
            peak_mem[0] = m
        time.sleep(0.001)  # 1ms 采样

img = Image.open(IMG_PATH)
if img.mode != "RGB":
    img = img.convert("RGB")
r = img.resize((1024, 1024), Image.BILINEAR)
a = np.array(r).astype(np.float32) / 255.0
a = (a - 0.5) / 0.5
a = a.transpose(2, 0, 1)[None, ...]

print(f"ONNX Runtime {ort.__version__}\n")

# 对比: 默认 vs 无arena
for name, opts in [
    ("默认(arena=ON)", {}),
    ("arena=OFF+4线程", {"enable_cpu_mem_arena": False, "intra_op_num_threads": 4}),
    ("arena=OFF+2线程", {"enable_cpu_mem_arena": False, "intra_op_num_threads": 2}),
    ("arena=OFF+1线程", {"enable_cpu_mem_arena": False, "intra_op_num_threads": 1}),
]:
    gc.collect()
    baseline = mb()
    print(f"--- {name} ---")
    print(f"  基线: {baseline:.1f} MB")

    so = ort.SessionOptions()
    for k, v in opts.items():
        setattr(so, k, v)
    sess = ort.InferenceSession(MODEL_PATH, so, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    after_load = mb()
    print(f"  加载后: {after_load:.1f} MB (+{after_load - baseline:.1f})")

    # 推理 (带监控)
    peak_mem[0] = after_load
    monitoring[0] = True
    t = threading.Thread(target=monitor)
    t.start()

    # warmup
    sess.run(None, {inp.name: a})

    # 正式推理 3 次
    times = []
    for i in range(3):
        t0 = time.time()
        sess.run(None, {inp.name: a})
        t1 = time.time()
        times.append((t1 - t0) * 1000)

    monitoring[0] = False
    t.join()

    peak = peak_mem[0]
    avg_time = sum(times) / len(times)
    print(f"  推理真实峰值: {peak:.1f} MB (增量 +{peak - baseline:.1f})")
    print(f"  推理耗时: {avg_time:.0f} ms (3次平均)")
    print(f"  峰值/模型加载: {peak / after_load:.2f}x")
    print()

    del sess
    gc.collect()
