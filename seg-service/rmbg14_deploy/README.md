# RMBG-1.4 ONNX 背景去除工具

基于 [BRIA AI RMBG-1.4](https://huggingface.co/briaai/RMBG-1.4) 模型，使用 ONNX Runtime 在 CPU 上进行人像/物体背景去除。

经过深入测试和优化，支持在 **2GB 内存 / 2 核 CPU** 的低配服务器上运行。

---

## 目录

- [核心发现](#核心发现)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [Web 服务使用](#web-服务使用)
- [模型说明](#模型说明)
- [内存优化方案](#内存优化方案)
- [基准测试结果](#基准测试结果)
- [API 接口](#api-接口)
- [常见问题](#常见问题)

---

## 核心发现

经过系统测试，得出以下关键结论：

### 1. 512x512 分辨率不可用

RMBG-1.4 的 IS-Net 架构在 1024x1024 分辨率训练。直接将输入降至 512x512 会导致：
- 最深层特征图仅 8x8，感受野覆盖整个图像
- 模型输出退化为均匀值（全部 ≈ 1.0），认为整张图都是前景
- **蒙版全白，无法抠出人像**

| 输入分辨率 | 输出范围 | 前景占比 | 结果 |
|-----------|---------|---------|------|
| 512x512 | [0.814, 1.000] | 100% | ❌ 全白 |
| 640x640 | [0.000, 1.000] | 44.8% | ✅ 正常 |
| 768x768 | [0.000, 1.000] | 44.5% | ✅ 正常 |
| 1024x1024 | [0.000, 1.000] | 44.5% | ✅ 正常 |

### 2. 动态分辨率模型

通过将原版 1024 模型的输入维度改为动态（`[1, 3, 'H', 'W']`），可在 640~1024 任意分辨率下正确推理。640x640 是最低可用分辨率。

### 3. 内存优化

ONNX Runtime 默认开启内存竞技场（arena），预分配大块内存导致峰值虚高。关闭后峰值大幅下降：

| 配置 | 推理真实峰值 | 耗时 |
|------|------------|------|
| arena=ON（默认） | 1520 MB | 5.1s |
| arena=OFF + 2线程 | **847 MB** | **3.5s** |

### 4. FP16/INT8 在 CPU 上不省内存

ONNX Runtime 的 CPUExecutionProvider 不原生支持 FP16/INT8 计算，内部反量化为 FP32 再算，反而增加内存：

| 精度 | 文件大小 | 推理峰值 | 效果 |
|------|---------|---------|------|
| FP32 | 168 MB | 558 MB | ✅ 正常 |
| FP16 | 84 MB | 639 MB | ✅ 正常（反而更高） |
| INT8 | 42 MB | 769 MB | ✅ 正常（反而更高） |

---

## 项目结构

```
rmbg14_deploy/
├── README.md                    # 本文档
├── requirements.txt             # Python 依赖
├── web_server.py                # Web 服务（Flask，支持动态分辨率）
├── make_dynamic.py              # 将 1024 固定模型转为动态输入模型
├── download_models.py           # 从 HuggingFace 下载模型文件
├── inference_demo.py            # 命令行推理示例
├── models/
│   ├── model.onnx               # FP32 1024x1024 原版模型 (168 MB)
│   ├── model_dynamic.onnx       # FP32 动态输入模型 (168 MB)
│   ├── model_fp16.onnx          # FP16 1024x1024 (84 MB)
│   └── model_quantized.onnx     # INT8 1024x1024 (42 MB)
├── benchmarks/
│   ├── bench_2g_v2.py            # 2G/2核 环境综合基准测试
│   ├── bench_peak_precise.py    # 精确峰值内存测量（线程监控）
│   └── bench_2g_v2_output.txt  # 基准测试结果
```

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 下载模型

```bash
python download_models.py
```

模型来源：`https://huggingface.co/briaai/RMBG-1.4`
国内用户默认使用镜像：`https://hf-mirror.com`

### 生成动态模型

```bash
python make_dynamic.py
```

将 `model.onnx`（固定 1024）转为 `model_dynamic.onnx`（动态输入），支持 640/768/1024 任意分辨率。

### 启动 Web 服务

```bash
# 默认 640x640（适合 2G/2核，峰值 ~361 MB）
python web_server.py

# 768x768（平衡模式，峰值 ~419 MB）
python web_server.py --size 768

# 1024x1024（最高精度，峰值 ~558 MB）
python web_server.py --size 1024

# 自定义端口
python web_server.py --size 640 --port 8080
```

浏览器打开 `http://localhost:5000` 即可使用。

---

## Web 服务使用

1. 打开浏览器访问服务地址
2. 点击上传区域或拖拽图片到上传区
3. 支持 JPG / PNG / WEBP 格式
4. 处理完成后显示三张图片：
   - 原始图片
   - 前景 Mask（黑白蒙版）
   - 去除背景后的透明 PNG
5. 点击「下载透明 PNG」保存结果

---

## 模型说明

### RMBG-1.4 架构

- 基于 IS-Net（Image Segmentation Network）
- 编码器-解码器结构，含 6 级下采样和 6 级上采样
- RSU（Residual U-block）模块 + skip connection + lateral connection
- Resize 操作使用动态 sizes（来自 Concat 节点），架构本身支持任意输入尺寸
- **但模型权重仅在 1024x1024 训练，低于 640 会出现退化**

### 模型文件

| 文件 | 精度 | 输入尺寸 | 文件大小 | 说明 |
|------|------|---------|---------|------|
| `model.onnx` | FP32 | 1024x1024 | 168 MB | HuggingFace 原版 |
| `model_dynamic.onnx` | FP32 | 动态 | 168 MB | 支持任意分辨率 |
| `model_fp16.onnx` | FP16 | 1024x1024 | 84 MB | 半精度 |
| `model_quantized.onnx` | INT8 | 1024x1024 | 42 MB | 量化版 |
| `model_512.onnx` | FP32 | 512x512 | 167 MB | ❌ 不可用（空白蒙版） |

---

## 内存优化方案

### 方案一：关闭内存竞技场（推荐）

```python
import onnxruntime as ort

so = ort.SessionOptions()
so.enable_cpu_mem_arena = False   # 关键：关闭内存竞技场
so.intra_op_num_threads = 2       # 2 线程
sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
```

**效果**：推理峰值从 1520 MB 降至 847 MB（1024 分辨率），降幅 44%。

### 方案二：降低输入分辨率（2G 服务器推荐）

使用动态模型 + 640x640 输入：

```python
# 预处理时 resize 到 640
r = img.resize((640, 640), Image.BILINEAR)
```

**效果**：推理峰值仅 361 MB，耗时 1.6s，抠图质量与 1024 接近。

### 方案三：组合方案（最优）

关闭 arena + 640 分辨率 + 2 线程：

| 指标 | 数值 |
|------|------|
| 模型加载后 RSS | ~233 MB |
| 推理真实峰值 | **~361 MB** |
| 推理耗时 | **~1.6s** |
| 抠图效果 | ✅ fg=44.8% |

在 2GB 内存服务器上，OS 占用约 400-500 MB，可用约 1500 MB，361 MB 峰值绰绰有余。

---

## 基准测试结果

### 测试环境

- CPU: 多核 x86_64
- Python: 3.10
- ONNX Runtime: 1.23.2
- Provider: CPUExecutionProvider
- 测试图片: 1046x1024 真人照片

### 综合对比（arena=OFF, 2 线程）

| 配置 | 模型文件 | 加载 RSS | 推理峰值 | 耗时 | 前景占比 | 结果 |
|------|---------|---------|---------|------|---------|------|
| FP32 1024 | 168 MB | 225 MB | **558 MB** | 3.9s | 44.5% | ✅ |
| FP32 768 | 168 MB | 232 MB | **419 MB** | 2.2s | 44.5% | ✅ |
| **FP32 640** | **168 MB** | **233 MB** | **361 MB** | **1.6s** | **44.8%** | ✅ |
| FP32 512 | 168 MB | 233 MB | 317 MB | 0.8s | 100% | ❌ |
| FP16 1024 | 84 MB | 237 MB | 639 MB | 4.1s | 44.5% | ✅ |
| INT8 1024 | 42 MB | 112 MB | 769 MB | 6.5s | 44.6% | ✅ |

### arena ON vs OFF 对比（1024 分辨率）

| 配置 | 推理真实峰值 | 耗时 |
|------|------------|------|
| arena=ON（默认） | **1520 MB** | 5.1s |
| arena=OFF + 4线程 | 848 MB | 7.4s |
| arena=OFF + 2线程 | **847 MB** | **3.5s** |
| arena=OFF + 1线程 | 846 MB | 6.9s |

### 不同分辨率输出对比

```
512:  output=[0.814, 1.000]  fg=100.0%  => FAIL (全白)
640:  output=[0.000, 1.000]  fg=44.8%   => OK
768:  output=[0.000, 1.000]  fg=44.5%   => OK
1024: output=[0.000, 1.000]  fg=44.5%   => OK
```

---

## API 接口

### `POST /predict`

上传图片并获取背景去除结果。

**请求**：`multipart/form-data`，字段 `image` 为图片文件

**响应**（JSON）：

```json
{
  "original": "<base64 JPEG>",
  "mask": "<base64 PNG>",
  "result": "<base64 PNG RGBA>",
  "width": 1046,
  "height": 1024,
  "inference_ms": 1600,
  "mask_mean": 113.7,
  "size": 640
}
```

**示例**：

```bash
curl -X POST -F "image=@photo.jpg" http://localhost:5000/predict
```

### `GET /`

返回 Web UI 页面。

---

## 常见问题

### Q: 512x512 分辨率为什么空白？

RMBG-1.4 的 IS-Net 在 1024x1024 训练，6 级下采样后最深特征图为 8x8（1024/128=8）。当输入降至 512，最深特征图仅 4x4，感受野过大覆盖全图，输出退化为均匀值。640 是最低可用分辨率（最深 5x5，勉强够用）。

### Q: FP16/INT8 为什么不省内存？

ONNX Runtime 的 CPUExecutionProvider 不原生支持 FP16/INT8 算术。模型加载时按原始精度存储（省文件空间），但推理时反量化为 FP32 计算，转换本身额外占用内存。要真正省内存需使用支持量化计算的 Provider（如 QNN、TensorRT）。

### Q: 如何在 2GB 服务器部署？

```bash
# 使用 640 分辨率（峰值仅 361 MB）
python web_server.py --size 640 --port 8080
```

操作系统占用约 400-500 MB，模型推理峰值 361 MB，总计约 900 MB，2GB 内存充足。

### Q: 如何切换到更高精度？

```bash
python web_server.py --size 1024    # 峰值 558 MB
python web_server.py --size 768     # 峰值 419 MB
```

### Q: 动态模型和原版模型有区别吗？

`model_dynamic.onnx` 和 `model.onnx` 权重完全相同，仅输入/输出维度标注从固定 1024 改为动态 `'H'/'W'`。在 1024 分辨率下两者输出一致。动态模型的优势是支持 640/768 等中间分辨率。

---

## 预处理/后处理说明

### 预处理

```python
img = img.convert("RGB")
img = img.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
arr = np.array(img).astype(np.float32) / 255.0
arr = (arr - 0.5) / 0.5           # normalize to [-1, 1]
arr = arr.transpose(2, 0, 1)[None, ...]  # HWC -> NCHW
```

### 后处理

```python
mask = output[0, 0]  # 提取单通道
mask = resize to original size (BILINEAR)
mask = (mask - min) / (max - min)  # min-max 归一化
mask = (mask * 255).astype(uint8)
# 应用为 alpha 通道
rgba.putalpha(Image.fromarray(mask, mode='L'))
```

模型输出已经过 Sigmoid 激活（范围 [0, 1]），无需额外处理。

---

## 依赖版本

```
onnx>=1.17.0
onnxruntime>=1.23.2
numpy>=1.24.0
Pillow>=10.0.0
psutil>=5.9.0
huggingface_hub>=0.24.0
flask>=3.0.0
```

---

## 参考链接

- 模型主页: https://huggingface.co/briaai/RMBG-1.4
- ONNX Runtime 文档: https://onnxruntime.ai/
- IS-Net 论文: https://github.com/xuebinqin/DIS
