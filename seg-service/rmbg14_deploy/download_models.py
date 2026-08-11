"""
下载 RMBG-1.4 ONNX 模型文件（FP32 + FP16）

用法:
    python download_models.py

模型来源: https://huggingface.co/briaai/RMBG-1.4
"""
import os
import ssl
import urllib.request

# ============================================================
# 模型下载配置
# ============================================================
HF_MIRROR = "https://hf-mirror.com"  # 国内镜像，海外用户改为 https://huggingface.co
MODELS = {
    # 原始 1024x1024 模型
    "model.onnx":         f"{HF_MIRROR}/briaai/RMBG-1.4/resolve/main/onnx/model.onnx",
    "model_fp16.onnx":    f"{HF_MIRROR}/briaai/RMBG-1.4/resolve/main/onnx/model_fp16.onnx",
    "model_quantized.onnx": f"{HF_MIRROR}/briaai/RMBG-1.4/resolve/main/onnx/model_quantized.onnx",
}

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

def download(url, out_path):
    """下载文件（跳过 SSL 验证）"""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 1024 * 1024:
        size = os.path.getsize(out_path) / 1024 / 1024
        print(f"  [SKIP] {os.path.basename(out_path)} 已存在 ({size:.1f} MB)")
        return

    print(f"  [DOWN] {os.path.basename(out_path)} <- {url}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(out_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded / total * 100
                    mb = downloaded / 1024 / 1024
                    print(f"\r  {pct:.0f}% ({mb:.1f} MB)", end="", flush=True)
            print()

    size = os.path.getsize(out_path) / 1024 / 1024
    print(f"  [OK]   {os.path.basename(out_path)} ({size:.1f} MB)")

def main():
    os.makedirs(MODELS_DIR, exist_ok=True)
    print("=" * 60)
    print("RMBG-1.4 ONNX 模型下载")
    print(f"镜像源: {HF_MIRROR}")
    print(f"保存到: {MODELS_DIR}")
    print("=" * 60)

    for filename, url in MODELS.items():
        out_path = os.path.join(MODELS_DIR, filename)
        try:
            download(url, out_path)
        except Exception as e:
            print(f"  [FAIL] {filename}: {e}")

    print("\n下载完成！文件列表:")
    for f in os.listdir(MODELS_DIR):
        p = os.path.join(MODELS_DIR, f)
        if os.path.isfile(p):
            print(f"  {f:30s} {os.path.getsize(p)/1024/1024:.1f} MB")

if __name__ == "__main__":
    main()
