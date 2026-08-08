---
name: video-generation
description: Generate videos through text-to-video, image-to-video, and keyframe animation.
---

# Video Generation

Use the `generate_video` tool when the user asks you to **create, generate, or make a video** (not an image).

If the `generate_video` tool is not available in the current tool list, tell the user that video generation is not enabled for this nanobot instance.

## When To Use

- **Text-to-video**: call `generate_video` with a `prompt` describing the scene. Add `duration` ("3s", "5s", "10s", "18s") for length.
- **Image-to-video**: pass an artifact ID in the `image` parameter to animate a static image.
- **Keyframe animation**: pass multiple artifact IDs in `keyframe_images` to create animated transitions between images.
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
│   ├── Has reference image? → use `image` param with artifact ID
│   ├── Multiple frames? → use `keyframe_images` param
│   └── Text only? → use `prompt` only
│
└── Wants static image → generate_image ✅
    └── Use image-generation skill guidance
```

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string (required) | Scene description in detail |
| `image` | string (optional) | Artifact ID of reference image for img2vid |
| `keyframe_images` | list[string] (optional) | Artifact IDs for keyframe animation mode |
| `duration` | string (optional) | "3s", "5s", "10s", "18s" (default: 5s) |
| `aspect_ratio` | string (optional) | "16:9", "9:16", "1:1", "4:3", "3:4" |
| `negative_prompt` | string (optional) | What to avoid in the video |

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
  image="1002",
  duration="3s"
)
```

Create keyframe animation from multiple images:

```text
generate_video(
  prompt="Smooth transition between these keyframes, cinematic fade",
  keyframe_images=["1010", "1011", "1012", "1013", "1014"],
  duration="10s"
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
