"""测试不同分辨率 + 精度在 2G2核下的表现 (使用动态模型)"""
import os, gc, time, threading
import numpy as np
from PIL import Image
import onnxruntime as ort
import psutil

import sys
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
IMG_PATH = sys.argv[1] if len(sys.argv) > 1 else None

proc = psutil.Process()
def mb():
    return proc.memory_info().rss / 1048576

if not IMG_PATH:
    print("用法: python bench_2g_v2.py <test_image.jpg>")
    sys.exit(1)
img = Image.open(IMG_PATH)
if img.mode != "RGB":
    img = img.convert("RGB")
print(f"img: {img.size}")

peak_mem = [0]
monitoring = [True]
def monitor():
    while monitoring[0]:
        m = mb()
        if m > peak_mem[0]:
            peak_mem[0] = m
        time.sleep(0.001)

def run_test(name, model_path, sz):
    gc.collect()
    baseline = mb()
    print(f"\n--- {name} (input={sz}) ---")
    print(f"  file: {os.path.getsize(model_path)/1048576:.1f} MB")

    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    so.intra_op_num_threads = 2
    sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    after_load = mb()
    print(f"  loaded: {after_load:.1f} MB (+{after_load-baseline:.1f})")

    r = img.resize((sz, sz), Image.BILINEAR)
    a = np.array(r).astype(np.float32) / 255.0
    a = (a - 0.5) / 0.5
    a = a.transpose(2, 0, 1)[None, ...]

    peak_mem[0] = after_load
    monitoring[0] = True
    t = threading.Thread(target=monitor)
    t.start()
    try:
        sess.run(None, {inp.name: a})  # warmup
        times = []
        for i in range(3):
            t0 = time.time()
            out = sess.run(None, {inp.name: a})
            t1 = time.time()
            times.append((t1 - t0) * 1000)
    except Exception as e:
        monitoring[0] = False
        t.join()
        print(f"  ERROR: {e}")
        del sess
        gc.collect()
        return
    monitoring[0] = False
    t.join()

    peak = peak_mem[0]
    avg_time = sum(times) / len(times)
    o = out[0]
    m = o[0, 0] if o.ndim == 4 else (o[0] if o.ndim == 3 else o)
    mp = Image.fromarray(m.astype(np.float32), mode='F').resize(img.size, Image.BILINEAR)
    ma = np.array(mp)
    mx, mn = ma.max(), ma.min()
    if mx > mn:
        ma = (ma - mn) / (mx - mn)
    mask = (ma * 255).astype(np.uint8)
    fg = (mask >= 204).sum() / mask.size * 100
    raw_range = o.max() - o.min()
    ok = "OK" if raw_range > 0.5 and 5 < fg < 95 else "FAIL"
    print(f"  peak: {peak:.1f} MB (+{peak-baseline:.1f})")
    print(f"  time: {avg_time:.0f} ms")
    print(f"  out: [{o.min():.4f},{o.max():.4f}] fg={fg:.1f}% range={raw_range:.4f} => {ok}")
    if ok == "OK":
        p = f"c:\\Users\\qinqingbiao\\onnx\\opt2_{name}.png"
        Image.fromarray(mask, mode='L').save(p)
        rgba = img.convert("RGBA").copy()
        rgba.putalpha(Image.fromarray(mask, mode='L'))
        rgba.save(f"c:\\Users\\qinqingbiao\\onnx\\opt2_{name}_result.png")
        print(f"  saved: {p}")
    del sess
    gc.collect()

DYN = os.path.join(MODEL_DIR, "model_dynamic.onnx")

print("\n=== FP32 Dynamic (arena=OFF, 2 threads) ===")
for sz in [1024, 768, 640, 512]:
    run_test(f"FP32-dyn-{sz}", DYN, sz)

print("\n=== FP16 (1024, arena=OFF) ===")
run_test("FP16-1024", os.path.join(MODEL_DIR, "model_fp16.onnx"), 1024)

print("\n=== INT8 (1024, arena=OFF) ===")
run_test("INT8-1024", os.path.join(MODEL_DIR, "model_quantized.onnx"), 1024)

print("\n=== Summary ===")
print("Best for 2G/2core: pick the smallest OK option")
