---
name: video-generation
description: Generate videos through text-to-video, image-to-video, multi-reference, and keyframe animation.
---

# Video Generation

Use the `generate_video` tool when the user asks you to **create, generate, or make a video** (not an image).

If the `generate_video` tool is not available in the current tool list, tell the user that video generation is not enabled for this nanobot instance.

## When To Use

- **Text-to-video**: call `generate_video` with a `prompt` describing the scene. Add `duration` ("3s", "5s", "10s", "18s") for length.
- **Image-to-video**: pass a single artifact ID in `reference_images` to animate a static image.
- **Multi-reference**: pass 2-4 artifact IDs in `reference_images` to use multiple images as style/content references (no time order).
- **Keyframe animation**: pass exactly 2 artifact IDs in `keyframe_images` (first frame + last frame) to generate a transition between them.
- After generating, the tool runs in background and auto-delivers the video when done.

## When NOT To Use

- **Do NOT use this tool when the user wants a static image.** Use `generate_image` instead.
- Do NOT use `generate_image` to "generate a video" — image tools produce **only** static pictures.
- If the user says "video", "动画", "动图", "clip", or mentions time duration (3s, 5s, 10s), they want `generate_video`.
- If the user says "图片", "image", "photo", "picture" without motion/animation context, they want `generate_image`.

## Tool Selection Decision Tree

```
User wants media?
├── Mentions "video"/"视频"/"动画"/duration → generate_video ✅
│   ├── Single image reference? → use `reference_images` with 1 item (img2vid)
│   ├── 2-4 images as references? → use `reference_images` with 2-4 items (multi_reference)
│   ├── First + last frame transition? → use `keyframe_images` with 2 items (keyframes)
│   └── Text only? → use `prompt` only (ti2vid)
│
└── Wants static image → generate_image ✅
    └── Use image-generation skill guidance
```

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string (required) | Scene description in detail |
| `reference_images` | list[string] (optional) | 1-4 artifact IDs for image-to-video or multi-reference mode |
| `keyframe_images` | list[string] (optional) | Exactly 2 artifact IDs (first frame + last frame) for keyframe animation |
| `duration` | string (optional) | "3s", "5s", "10s", "18s" (default: 5s) |
| `aspect_ratio` | string (optional) | "16:9", "9:16", "1:1", "4:3", "3:4" (default: 16:9) |
| `num_frames` | int (optional) | Direct frame count, overrides duration preset |
| `frame_rate` | int (optional) | Direct frame rate, overrides duration preset |
| `num_inference_steps` | int (optional) | Inference steps for quality/speed balance |
| `negative_prompt` | string (optional) | What to avoid in the video |
| `seed` | int (optional) | Random seed for reproducible results |

### Mode Auto-Inference

The `mode` parameter is automatically inferred from the image parameters — you do NOT need to set it manually:

| Image Parameter | Count | Auto Mode |
|---|---|---|
| none | — | `ti2vid` |
| `reference_images` | 1 | `img2vid` |
| `reference_images` | 2-4 | `multi_reference` |
| `keyframe_images` | 2 | `keyframes` |

If both `reference_images` and `keyframe_images` are passed, `reference_images` takes priority.

### Parameter Extraction from Natural Language

When the user describes parameters in natural language, extract them:

| User Says | Parameter |
|---|---|
| "5秒", "5 seconds" | `duration="5s"` |
| "16:9", "横版", "landscape" | `aspect_ratio="16:9"` |
| "9:16", "竖版", "portrait", "短视频" | `aspect_ratio="9:16"` |
| "方形" | `aspect_ratio="1:1"` |
| "根据 1001 和 1002" | `reference_images=["1001", "1002"]` |
| "用 1001 和 1002 做首尾帧" | `keyframe_images=["1001", "1002"]` |
| "用图 1001" | `reference_images=["1001"]` |

## Prompt Writing Tips

Video prompts should describe:

- **Motion and movement**: "camera slowly zooms in", "planet rotating left", "waves crashing"
- **Scene dynamics**: weather changes, lighting transitions, particle effects
- **Temporal flow**: beginning → middle → end sequence
- **Camera work**: pan, tilt, zoom, tracking shot, static

Example good prompt:
```
"A majestic gas giant planet with glowing rings, slowly rotating in space.
Camera orbits from right to left over 3 seconds. Particle dust floats in the rings.
Background stars twinkle. Cinematic lighting with lens flare."
```

## Examples

Generate a 5-second video from text:

```text
generate_video(
  prompt="A cat chasing a butterfly in a sunlit garden, slow motion, 5 seconds",
  duration="5s",
  aspect_ratio="16:9"
)
```

Animate an existing image into 3-second video:

```text
generate_video(
  prompt="Make this image come alive: water ripples, leaves sway gently",
  reference_images=["1002"],
  duration="3s"
)
```

Generate video from two reference images:

```text
generate_video(
  prompt="Create a showcase video combining elements from both images, cinematic style",
  reference_images=["1001", "1002"],
  duration="5s",
  aspect_ratio="16:9"
)
```

Create keyframe animation from first and last frame:

```text
generate_video(
  prompt="Smooth cinematic transition between these two frames",
  keyframe_images=["1010", "1011"],
  duration="3s"
)
```

## Post-Generation

The `generate_video` tool runs **asynchronously**:
1. It returns immediately with a task ID
2. The system polls in background every ~10 seconds
3. When complete, the video is **automatically pushed** to the user's chat
4. You do NOT need to call `message` to deliver the video — it's handled automatically

Tell the user: "🎬 视频正在生成中... 预计需要 1-5 分钟，完成后会自动发送给你。"

## Artifact Rules

Videos are registered as artifacts with type "video" (IDs starting with `2`, e.g., `2001`).
Images used as video references keep their original "image" artifact IDs (starting with `1`).
