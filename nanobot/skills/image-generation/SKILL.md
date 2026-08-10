---
name: image-generation
description: Generate images and iteratively edit saved image artifacts.
---

# Image Generation
Use the `generate_image` tool when the user asks you to create, render, draw, design, generate, or edit an **image** (static picture).

**IMPORTANT**: If the user wants a **video**, **animation**, or **motion clip** (mentions "视频", "动画", duration like 3s/5s), use `generate_video` instead. See video-generation skill for details.

**IMPORTANT**: If the user wants to **extract a subject** from an image, **remove background**, or **segment** an image (mentions "提取主体", "去背景", "抠图", "分离主体"), see the **subject-extraction** skill instead. Do not use `generate_image` for subject extraction unless the user explicitly chooses the "AI 生成" mode after being asked.

If the `generate_image` tool is not available in the current tool list, tell the user that image generation is not enabled for this nanobot instance.

## When To Use

- Text-to-image: call `generate_image` with a concrete `prompt`.
- Image editing: pass the saved artifact path or user image path in `reference_images`.
- Iterative edits in the same conversation: prefer the most recent generated image artifact if the user says things like "make it brighter", "change the background", or "try another version".
- Ambiguous edits: ask a short clarifying question if multiple recent images could be the target.
- After generating images, call the `message` tool with the artifact paths in the `media` parameter to deliver them to the user.

## When NOT To Use

**CRITICAL**: Do not generate images just because the user uploaded one.

- Uploading an image is **NOT** a request to generate similar images.
- If the user sends only an image without text instructions, you **MUST** first analyze
  and describe the image, then ask what they want to do with it (describe, edit, answer
  a question, etc.) before calling any generation tool.
- Do not proactively generate multiple variations unless the user explicitly asks for options.
- Do not generate keyframe images or related media unless the user explicitly requests a
  video or animation that requires them.
- These rules override any implicit interpretation of user intent. When in doubt, ask.

## Parameter Extraction from Natural Language

When the user describes parameters in natural language, extract them:

| User Says | Parameter |
|---|---|
| "16:9", "横版", "landscape", "宽屏" | `aspect_ratio="16:9"` |
| "9:16", "竖版", "portrait", "手机壁纸" | `aspect_ratio="9:16"` |
| "方形", "正方形", "square" | `aspect_ratio="1:1"` |
| "4:3", "传统比例" | `aspect_ratio="4:3"` |
| "4K", "超高清", "ultra HD" | `image_size="4K"` |
| "2K", "高清", "HD" | `image_size="2K"` |
| "1K", "标准", "standard" | `image_size="1K"` |
| "1024x1024" (explicit dimensions) | `image_size="1024x1024"` |

If the user does not specify any size parameters, use the tool defaults (`aspect_ratio` and `image_size` from config).

## Prompt Rules

Write prompts with enough detail for image models:

- Subject and scene.
- Composition and camera or layout.
- Style, mood, lighting, and color palette.
- Text that must appear in the image, quoted exactly.
- Constraints such as "keep the same character", "preserve the logo", or "do not change the background".

## Artifact Rules

The tool stores generated images as persistent artifacts under nanobot's media directory and returns structured metadata:

- `id`: generated image id, such as `img_ab12cd34ef56`.
- `path`: local file path for internal follow-up edits.
- `mime`: image MIME type.
- `prompt`, `model`, and `source_images`: provenance for follow-up edits.

In normal user-facing replies, do not expose local filesystem paths. Keep the reply natural, for example "Done, I generated it." You may include the short image `id` when it helps the user refer to a specific image, but keep raw `path` internal unless the user explicitly asks for debug details or a local artifact reference. Never paste base64.

For follow-up edits, pass the prior artifact `path` to `reference_images`. If the user provides a new uploaded image, use that path as the reference instead.

Do not include internal replay markers such as `[Message Time: ...]`, `[image: /local/path]`, `generate_image(...)`, or `message(...)` in user-facing replies.

## Examples

Generate a new image with parameters from natural language:

```text
// User: "生成一张 16:9 的横版壁纸，4K 高清"
generate_image(
  prompt="A minimal app icon for nanobot: friendly robot head, rounded square, soft blue and white palette, clean vector style, no text",
  aspect_ratio="16:9",
  image_size="4K"
)
```

Edit the latest generated artifact:

```text
generate_image(
  prompt="Use the reference image. Keep the same robot and composition, but change the palette to warm orange and add a subtle sunrise background.",
  reference_images=["/home/user/.nanobot/media/generated/2026-05-08/img_ab12cd34ef56.png"],
  aspect_ratio="1:1",
  image_size="1K"
)
```
