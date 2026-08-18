# HyperFrames 视频生成项目

基于 [HyperFrames](https://github.com/heygen-com/hyperframes) 开源框架，集成 [nanobot](https://github.com/HKUDS/nanobot) AI Agent，实现 **HTML 合成 → MP4 视频 + TTS 配音** 的全自动视频制作流程。

## 核心特性

- **HTML 即视频**：用 HTML/CSS/GSAP 编写视频画面，`data-start` 和 `data-duration` 控制时间轴
- **AI 一键生成**：nanobot Agent 根据自然语言描述自动编写 HTML 并渲染
- **本地 TTS 配音**：基于 Kokoro-82M 本地 AI 语音模型，支持中文语音 `zf_xiaobei`，无需网络
- **确定性渲染**：相同的 HTML 输入始终产生相同的 MP4 输出
- **自动交付**：渲染完成后视频自动发送给用户

## 项目结构

```
/home/nanobot/hyperframes/
├── package.json              # HyperFrames NPM 依赖配置
├── README.md                 # 本文件
├── templates/                # HTML 视频模板
│   └── example.html         # 示例视频模板
├── compositions/             # HyperFrames 合成项目
│   └── example/             # 示例合成项目
│       ├── index.html        # 合成文件
│       ├── hyperframes.json  # 配置
│       └── meta.json         # 元数据
├── output/                   # 最终输出的 MP4 视频
├── audio/                    # TTS 配音音频
└── scripts/                  # 工具脚本
```

## 环境要求

- **Node.js** >= 20（运行 HyperFrames CLI）
- **Chrome 浏览器**（HyperFrames 使用 Chrome 渲染，运行 `hyperframes browser ensure` 下载）
- **FFmpeg**（视频编码和音视频合并）
- **Python** >= 3.11（运行 nanobot）

## 安装

### 1. 安装 HyperFrames NPM 依赖

```bash
cd /home/nanobot/hyperframes
npm install --ignore-scripts
```

> `--ignore-scripts` 跳过 onnxruntime-node 的安装脚本（该脚本需要网络访问下载二进制文件）。
> HyperFrames TTS 使用 Kokoro-82M 模型，会在首次运行时自动下载。

### 2. 确保 Chrome 浏览器

```bash
cd /home/nanobot/hyperframes
npx hyperframes browser ensure
```

### 3. 安装 FFmpeg

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

### 4. 验证环境

```bash
cd /home/nanobot/hyperframes
npx hyperframes doctor
```

### 5. 安装 nanobot

```bash
cd /home/nanobot
pip install -e .
```

## 代理配置（可选）

如果需要通过代理访问网络（下载依赖、Chrome 浏览器等）：

```bash
# 一键设置
source /home/nanobot/hyperframes/scripts/setup-proxy.sh

# 或手动设置
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

nanobot 配置文件中 HyperFrames 工具的 `proxy` 字段会传递给子进程：

```json
{
  "tools": {
    "hyperframes": {
      "enabled": true,
      "proxy": "http://127.0.0.1:7890"
    }
  }
}
```

## 使用方式

### 方式一：通过 nanobot AI Agent（推荐）

1. 启动 nanobot：

```bash
cd /home/nanobot
nanobot webui
```

2. 在 WebUI 中对 nanobot 说：

> "帮我做一个产品介绍视频，用 HTML 渲染，5 秒标题动画，加上中文配音"

nanobot 会自动：
- 调用 `create_composition` 创建项目
- 编写 HTML 内容
- 调用 `render_video` 渲染为 MP4
- 调用 `generate_tts_audio` 生成中文配音
- 调用 `merge_video_audio` 合并视频和配音
- 自动将最终视频发送给你

### 方式二：手动命令行

```bash
# 1. 创建新项目
cd /home/nanobot/hyperframes
npx hyperframes init my-video --example blank --non-interactive --skip-skills

# 2. 编辑合成文件
# 编辑 compositions/my-video/index.html

# 3. 验证
npx hyperframes lint compositions/my-video

# 4. 渲染为视频
npx hyperframes render compositions/my-video -o output/video.mp4 -f 30 -q standard

# 5. 生成 TTS 配音
npx hyperframes tts "欢迎了解 HyperFrames" -o audio/speech.wav -v zf_xiaobei -s 1.0

# 6. 合并视频和音频
ffmpeg -y -i output/video.mp4 -i audio/speech.wav \
  -c:v copy -c:a aac -b:a 192k -shortest output/final.mp4
```

## HTML 合成规范

HyperFrames 合成项目使用 `data-*` 属性控制视频时间轴：

```html
<div id="root"
     data-composition-id="main"
     data-start="0"
     data-duration="10"
     data-width="1920"
     data-height="1080">

  <div class="clip" data-start="0" data-duration="4" data-track-index="1">
    <!-- 场景内容 -->
  </div>

  <div class="clip" data-start="4" data-duration="6" data-track-index="1">
    <!-- 场景内容 -->
  </div>
</div>
```

| 属性 | 说明 |
|------|------|
| `data-composition-id` | 根组合 ID（通常为 `main`） |
| `data-start` | 元素开始时间（秒） |
| `data-duration` | 元素持续时间（秒） |
| `data-track-index` | 轨道层级（控制图层顺序） |
| `data-width` / `data-height` | 视频分辨率 |

### GSAP 动画

使用 GSAP 时间轴实现动画，存储在 `window.__timelines` 中：

```javascript
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
tl.from(".title", { opacity: 0, y: -50, duration: 1 }, 0);
window.__timelines["main"] = tl;
```

## nanobot 工具 API

### create_composition

创建新的合成项目。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 项目名称 |
| `example` | string | 否 | 模板: `blank`, `warm-grain`, `swiss-grid` |

### render_video

渲染项目为 MP4 视频。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_dir` | string | 是 | 项目目录路径 |
| `output_path` | string | 否 | 输出路径 |
| `fps` | int | 否 | 帧率: 24, 30, 60 |
| `quality` | string | 否 | 质量: `draft`, `standard`, `high` |
| `output_format` | string | 否 | 格式: `mp4`, `webm`, `mov` |

### generate_tts_audio

生成 TTS 配音音频。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | 是 | 配音文本 |
| `output_path` | string | 否 | 输出路径 |
| `voice` | string | 否 | 语音 ID |
| `speed` | string | 否 | 语速倍数 |
| `lang` | string | 否 | 语言代码 |

### merge_video_audio

合并视频和音频。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `video_path` | string | 是 | 视频文件路径 |
| `audio_path` | string | 是 | 音频文件路径 |
| `output_path` | string | 否 | 输出路径 |

### lint_composition

验证合成项目。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_dir` | string | 是 | 项目目录路径 |

### list_compositions

列出所有合成项目。无参数。

## 完整示例

用户对 nanobot 说：

> "帮我做一个 HyperFrames 介绍视频，12 秒，3 个场景，加上中文配音"

nanobot 执行：

1. `create_composition(name="intro")` → 创建项目
2. 使用 `write_file` 编写 `index.html`（3 个场景，每个 4 秒）
3. `lint_composition(project_dir="...")` → 验证
4. `render_video(project_dir="...")` → `renders/intro.mp4`
5. `generate_tts_audio(text="HyperFrames 是用 HTML 创作视频的开源框架...")` → `audio/speech.wav`
6. `merge_video_audio(video_path="...", audio_path="...")` → `output/final.mp4`
7. 最终视频自动发送给用户
