---
name: hyperframes
description: "用 HTML 生成视频并添加 TTS 配音。通过 HyperFrames 渲染框架将 HTML/CSS/GSAP 合成项目渲染为 MP4 视频，配合内置 Kokoro-82M AI 语音模型生成中文配音。"
metadata: {"nanobot":{"emoji":"🎬","requires":{"bins":["node","ffmpeg"]}}}
---

# HyperFrames 视频生成技能

使用 HyperFrames 框架将 HTML 合成项目渲染为 MP4 视频，并可选通过内置 Kokoro-82M AI 模型添加中文配音。

## 何时使用

- 用户要求"制作视频"、"生成视频"、"渲染视频"（涉及 HTML → MP4）
- 用户要求添加"配音"、"旁白"、"语音 narration"
- 用户提供 HTML 模板或描述视频内容
- 用户要求"做动画"、"做片头"、"做介绍视频"

## 何时不用

- 用户只想生成 AI 视频（使用 `generate_video` 工具，基于文生视频模型）
- 用户只想生成图片（使用 `generate_image` 工具）
- 用户只发了图片没有明确要求做视频

## 前置要求

- **Node.js** >= 20（运行 HyperFrames CLI）
- **FFmpeg**（视频编码和音视频合并）
- **Chrome 浏览器**（HyperFrames 使用 Chrome 渲染，运行 `hyperframes browser ensure` 下载）

## 工具

### create_composition

创建新的 HyperFrames 视频合成项目。

**参数：**
- `name` (必填): 项目名称（英文，如 `my-video`）
- `example` (可选): 示例模板，可选 `blank`、`warm-grain`、`swiss-grid`。默认 `blank`

### render_video

将 HyperFrames 项目渲染为 MP4 视频。

**参数：**
- `project_dir` (必填): 项目目录路径，如 `/home/nanobot/hyperframes/compositions/my-video`
- `output_path` (可选): 输出 MP4 路径，默认 `renders/<name>.mp4`
- `fps` (可选): 帧率，可选 24、30、60，默认 30
- `quality` (可选): 质量 `draft`/`standard`/`high`，默认 `standard`
- `output_format` (可选): 格式 `mp4`/`webm`/`mov`，默认 `mp4`

### generate_tts_audio

使用 Kokoro-82M 本地 AI 模型生成 TTS 配音。

**参数：**
- `text` (必填): 配音文本（支持中英文）
- `output_path` (可选): 输出路径，默认 `audio/speech.wav`
- `voice` (可选): 语音 ID，默认 `zf_xiaobei`（中文小贝）
- `speed` (可选): 语速倍数，如 `1.0`、`1.2`、`0.8`
- `lang` (可选): 语言代码 `zh`/`en-us`/`ja` 等

可用语音：

| 语音 ID | 语言 | 说明 |
|---------|------|------|
| `zf_xiaobei` | 中文 | 小贝（默认） |
| `af_heart` | 英文 | Heart |
| `af_nova` | 英文 | Nova |
| `af_sky` | 英文 | Sky |
| `am_adam` | 英文 | Adam |
| `am_michael` | 英文 | Michael |
| `bf_emma` | 英文 | Emma |
| `jf_alpha` | 日文 | Alpha |

### merge_video_audio

使用 FFmpeg 合并视频和音频。

**参数：**
- `video_path` (必填): 视频文件路径
- `audio_path` (必填): 音频文件路径
- `output_path` (可选): 输出路径，默认 `output/final.mp4`

### lint_composition

验证合成项目是否有常见错误。

**参数：**
- `project_dir` (必填): 项目目录路径

### list_compositions

列出所有已创建的合成项目。无参数。

## 工作流程

### 完整流程（视频 + 配音）

1. `create_composition(name="my-video")` → 创建项目
2. 使用 `write_file` 编辑 `index.html` 编写视频内容
3. `lint_composition(project_dir="...")` → 验证组合
4. `render_video(project_dir="...")` → 渲染为 MP4
5. `generate_tts_audio(text="...")` → 生成配音
6. `merge_video_audio(video_path="...", audio_path="...")` → 合并
7. 最终视频自动发送给用户

### 仅视频

1. `create_composition(name="my-video")` → 创建项目
2. 编辑 `index.html`
3. `render_video(project_dir="...")` → 渲染并发送

## HTML 合成规范

HyperFrames 合成项目使用 `data-*` 属性控制时间轴：

```html
<!doctype html>
<html lang="zh">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body {
        width: 1920px; height: 1080px;
        overflow: hidden; background: #000;
      }
    </style>
  </head>
  <body>
    <div id="root"
         data-composition-id="main"
         data-start="0"
         data-duration="10"
         data-width="1920"
         data-height="1080">

      <!-- 场景1: 0-4秒 -->
      <div class="clip" data-start="0" data-duration="4" data-track-index="1"
           style="font-size: 96px; color: #fff; text-align: center; padding: 200px">
        标题动画
      </div>

      <!-- 场景2: 4-10秒 -->
      <div class="clip" data-start="4" data-duration="6" data-track-index="1"
           style="font-size: 64px; color: #fff; text-align: center; padding: 100px">
        内容展示
      </div>
    </div>

    <script>
      // GSAP 动画时间轴
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      // tl.from(".clip", { opacity: 0, y: -50, duration: 1 }, 0);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
```

### 关键属性

| 属性 | 说明 |
|------|------|
| `data-composition-id` | 根组合 ID（通常为 `main`） |
| `data-start` | 元素开始时间（秒） |
| `data-duration` | 元素持续时间（秒） |
| `data-track-index` | 轨道层级（控制图层顺序） |
| `data-width` / `data-height` | 视频分辨率 |

### 动画

- 使用 **GSAP** 时间轴实现动画
- 将时间轴存储在 `window.__timelines["<composition-id>"]` 中
- 时间轴必须 `paused: true`，HyperFrames 会自动驱动

## 示例

用户："帮我做一个产品介绍视频，10 秒，2 个场景，加上中文配音"

```text
// 1. 创建项目
create_composition(name="product-intro")

// 2. 编辑 HTML（使用 write_file 工具）
// 写入 /home/nanobot/hyperframes/compositions/product-intro/index.html
// 包含 2 个场景，每个 5 秒

// 3. 验证
lint_composition(project_dir="/home/nanobot/hyperframes/compositions/product-intro")

// 4. 渲染视频
render_video(
  project_dir="/home/nanobot/hyperframes/compositions/product-intro",
  fps=30,
  quality="standard"
)

// 5. 生成配音
generate_tts_audio(
  text="欢迎了解我们的产品，这是一款革命性的工具。",
  voice="zf_xiaobei",
  speed="1.0"
)

// 6. 合并
merge_video_audio(
  video_path="/home/nanobot/hyperframes/compositions/product-intro/renders/product-intro.mp4",
  audio_path="/home/nanobot/hyperframes/audio/speech.wav"
)
// 最终视频自动发送给用户
```
