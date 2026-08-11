"""
RMBG-1.4 ONNX 推理示例 (命令行)

支持 FP32 动态分辨率模型，内置内存优化。

用法:
    python inference_demo.py -i photo.jpg -o no_bg.png --size 640
    python inference_demo.py -i photo.jpg -o no_bg.png --size 1024 --model fp32

参数:
    --input      输入图片路径
    --output     输出图片路径 (默认: no_bg.png)
    --model      模型类型: fp32 / fp16 / int8 (默认: fp32)
    --size       输入尺寸: 640 / 768 / 1024 (默认: 640)
    --model-dir  模型文件目录 (默认: ./models)
    --threads    推理线程数 (默认: 2)
"""
import os
import sys
import argparse
import time
import numpy as np
from PIL import Image
import onnxruntime as ort


def get_model_path(model_type, model_dir, size):
    """根据类型和尺寸选择模型文件"""
    if size == 1024 and model_type == "fp32":
        filename = "model.onnx"
    elif model_type == "fp32":
        filename = "model_dynamic.onnx"
    elif model_type == "fp16":
        filename = "model_fp16.onnx"
    elif model_type == "int8":
        filename = "model_quantized.onnx"
    else:
        filename = "model_dynamic.onnx"

    path = os.path.join(model_dir, filename)
    if not os.path.exists(path):
        print(f"[ERROR] 模型文件不存在: {path}")
        print(f"        请先运行: python download_models.py")
        sys.exit(1)
    return path


def preprocess(pil_img, target_size):
    """预处理: resize + normalize"""
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    ow, oh = pil_img.size
    r = pil_img.resize((target_size, target_size), Image.BILINEAR)
    a = np.array(r).astype(np.float32) / 255.0
    a = (a - 0.5) / 0.5
    a = a.transpose(2, 0, 1)[None, ...]
    return a, (ow, oh)


def postprocess(result, orig_size):
    """后处理: resize 回原始尺寸 + 归一化"""
    ow, oh = orig_size
    m = result[0, 0] if result.ndim == 4 else (result[0] if result.ndim == 3 else result)
    mp = Image.fromarray(m.astype(np.float32), mode='F').resize((ow, oh), Image.BILINEAR)
    ma = np.array(mp)
    mx, mn = ma.max(), ma.min()
    if mx > mn:
        ma = (ma - mn) / (mx - mn)
    return (ma * 255).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser(description="RMBG-1.4 ONNX 背景去除")
    parser.add_argument("--input", "-i", required=True, help="输入图片路径")
    parser.add_argument("--output", "-o", default="no_bg.png", help="输出图片路径")
    parser.add_argument("--model", "-m", default="fp32",
                        choices=["fp32", "fp16", "int8"], help="模型类型 (默认: fp32)")
    parser.add_argument("--size", "-s", type=int, default=640,
                        choices=[640, 768, 1024], help="输入尺寸 (默认: 640)")
    parser.add_argument("--model-dir", default="models", help="模型目录")
    parser.add_argument("--threads", type=int, default=2, help="推理线程数")
    args = parser.parse_args()

    # 加载模型
    model_path = get_model_path(args.model, args.model_dir, args.size)
    file_size = os.path.getsize(model_path) / 1024 / 1024
    print(f"[1/4] 模型: {os.path.basename(model_path)} ({file_size:.1f} MB)")

    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False  # 关闭内存竞技场, 大幅降低峰值
    so.intra_op_num_threads = args.threads
    sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name
    print(f"      输入: {input_name} shape={sess.get_inputs()[0].shape}")
    print(f"      分辨率: {args.size}x{args.size} | 线程: {args.threads}")

    # 读取图片
    print(f"[2/4] 读取: {args.input}")
    img = Image.open(args.input)
    print(f"      原始尺寸: {img.size[0]}x{img.size[1]}")

    # 预处理 + 推理
    print(f"[3/4] 推理中...")
    inp, osz = preprocess(img, args.size)
    t0 = time.time()
    out = sess.run(None, {input_name: inp})
    t1 = time.time()
    print(f"      耗时: {(t1-t0)*1000:.0f} ms")

    # 后处理
    mask = postprocess(out[0], osz)
    print(f"      Mask: min={mask.min()} max={mask.max()} mean={mask.mean():.1f}")

    # 保存
    print(f"[4/4] 保存: {args.output}")
    rgba = img.convert("RGBA").copy()
    rgba.putalpha(Image.fromarray(mask, mode='L'))
    rgba.save(args.output)
    print(f"      完成!")


if __name__ == "__main__":
    main()
