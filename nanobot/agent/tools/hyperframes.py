"""HyperFrames tool — render HTML compositions to MP4 video with optional TTS.

Tools:
  - create_composition:  scaffold a new HyperFrames project (npx hyperframes init)
  - render_video:        project dir → MP4 (npx hyperframes render)
  - generate_tts_audio:  text → WAV/MP3 (npx hyperframes tts, Kokoro-82M local model)
  - merge_video_audio:   combine MP4 + audio → final MP4 (ffmpeg)
  - lint_composition:    validate a composition (npx hyperframes lint)
"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.config_base import Base

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.bus.queue import MessageBus
    from nanobot.session.manager import SessionManager

# Default project root for HyperFrames assets
_DEFAULT_PROJECT_ROOT = "/home/nanobot/hyperframes"

# HyperFrames CLI version (pinned for reproducibility)
_HF_CLI_VERSION = "0.4.45"


class HyperFramesToolConfig(Base):
    """HyperFrames tool configuration."""

    enabled: bool = True
    project_root: str = _DEFAULT_PROJECT_ROOT
    default_fps: int = 30
    default_quality: str = "standard"  # draft, standard, high
    default_format: str = "mp4"  # mp4, webm, mov
    default_voice: str = "zf_xiaobei"  # Kokoro-82M Chinese voice
    default_speed: float = 1.0  # TTS speed multiplier
    render_timeout: int = 600  # seconds (rendering can be slow)
    tts_timeout: int = 120  # seconds
    merge_timeout: int = 120  # seconds
    init_timeout: int = 60  # seconds
    workers: int = 1  # number of Chrome render workers; 1 for low-memory servers
    no_browser_gpu: bool = True  # disable Chrome GPU accel to save memory
    proxy: str = ""  # e.g. http://127.0.0.1:7890
    auto_deliver: bool = True  # auto-push rendered video to user via MessageBus


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _build_cli_cmd(
    subcommand: str,
    *,
    project_root: Path,
    cli_version: str = _HF_CLI_VERSION,
    use_npx: bool = True,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a hyperframes CLI command.

    If use_npx is True, uses ``npx --yes hyperframes@<version> <subcommand>``.
    Otherwise, uses the local node_modules binary.
    """
    local_bin = project_root / "node_modules" / ".bin" / "hyperframes"
    if use_npx and not local_bin.exists():
        cmd = ["npx", "--yes", f"hyperframes@{cli_version}", subcommand]
    else:
        # Use local binary if available (faster, no network)
        local_cli = project_root / "node_modules" / "hyperframes" / "dist" / "cli.js"
        if local_cli.exists():
            cmd = ["node", str(local_cli), subcommand]
        else:
            cmd = ["npx", "--yes", f"hyperframes@{cli_version}", subcommand]

    if extra_args:
        cmd.extend(extra_args)
    return cmd


def _build_env(proxy: str = "") -> dict[str, str]:
    """Build environment for subprocess with optional proxy."""
    env = os.environ.copy()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
        logger.info("HyperFrames using proxy: {}", proxy)
    return env


async def _run_cli(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    label: str,
) -> tuple[int, str, str]:
    """Run a CLI command and return (exit_code, stdout, stderr)."""
    logger.info("Running {}: {}", label, " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        return (proc.returncode or 0, out, err)
    except asyncio.TimeoutError:
        raise
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Command not found: {exc}") from exc


# ---------------------------------------------------------------------------
# create_composition
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        required=["name"],
        name=StringSchema(
            "项目名称（英文，如 my-video, product-intro）。"
            "会在 project_root/compositions/<name> 下创建项目。",
            min_length=1,
        ),
        example=StringSchema(
            "示例模板名称。可选: blank, warm-grain, swiss-grid。默认 blank。",
            nullable=True,
        ),
    )
)
class CreateCompositionTool(Tool):
    """Scaffold a new HyperFrames composition project."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(config=ctx.config.hyperframes)

    def __init__(self, *, config: HyperFramesToolConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "create_composition"

    @property
    def description(self) -> str:
        return (
            "创建一个新的 HyperFrames 视频合成项目。"
            "会在 project_root/compositions/<name> 下生成项目结构（index.html, hyperframes.json 等）。"
            "创建后可编辑 index.html 编写视频内容，然后用 render_video 渲染。"
        )

    async def execute(
        self,
        name: str,
        example: str | None = None,
        **kwargs: Any,
    ) -> str:
        project_root = Path(self.config.project_root)
        compositions_dir = project_root / "compositions"
        compositions_dir.mkdir(parents=True, exist_ok=True)

        target_dir = compositions_dir / name
        if target_dir.exists():
            return ToolResult.error(
                f"Error: Directory already exists: {target_dir}"
            )

        # Check node
        node_bin = shutil.which("node")
        if not node_bin:
            return ToolResult.error(
                "Error: Node.js is not installed. Install Node.js >= 20 to use HyperFrames."
            )

        # Build init command
        extra_args = [
            str(target_dir),
            "--non-interactive",
            "--skip-skills",
        ]
        if example:
            extra_args.extend(["--example", example])

        cmd = _build_cli_cmd("init", project_root=project_root, extra_args=extra_args)
        env = _build_env(self.config.proxy)

        try:
            exit_code, stdout, stderr = await _run_cli(
                cmd, cwd=str(project_root), env=env,
                timeout=self.config.init_timeout, label="HyperFrames init",
            )
        except asyncio.TimeoutError:
            return ToolResult.error(
                f"Error: HyperFrames init timed out after {self.config.init_timeout} seconds."
            )
        except FileNotFoundError as exc:
            return ToolResult.error(f"Error: {exc}")

        if exit_code != 0:
            return ToolResult.error(
                f"Error: HyperFrames init failed (exit code {exit_code}).\n"
                f"stdout: {stdout[:2000]}\n"
                f"stderr: {stderr[:2000]}"
            )

        # Verify project was created
        index_html = target_dir / "index.html"
        if not index_html.exists():
            return ToolResult.error(
                f"Error: Init completed but index.html not found in {target_dir}"
            )

        return (
            f"✅ 项目创建成功！\n"
            f"**项目路径**: `{target_dir}`\n"
            f"**入口文件**: `{index_html}`\n\n"
            f"编辑 `{index_html}` 编写视频内容，然后调用 `render_video` 渲染为 MP4。\n"
            f"使用 `data-start` 和 `data-duration` 属性控制时间轴。"
        )


# ---------------------------------------------------------------------------
# render_video
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        required=["project_dir"],
        project_dir=StringSchema(
            "HyperFrames 项目目录路径。例如 /home/nanobot/hyperframes/compositions/my-video。"
            "目录中需包含 index.html（合成文件）。",
            min_length=1,
        ),
        output_path=StringSchema(
            "输出 MP4 路径。默认 renders/<name>.mp4。",
            nullable=True,
        ),
        fps=IntegerSchema(
            description="帧率: 24, 30, 60。默认 30。",
            nullable=True,
        ),
        quality=StringSchema(
            "渲染质量: draft (快速预览), standard (标准), high (高质量)。默认 standard。",
            nullable=True,
        ),
        output_format=StringSchema(
            "输出格式: mp4, webm, mov。默认 mp4。",
            nullable=True,
        ),
    )
)
class RenderVideoTool(Tool):
    """Render a HyperFrames composition to MP4 using the HyperFrames CLI."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(
            config=ctx.config.hyperframes,
            bus=ctx.bus,
            sessions=ctx.sessions,
        )

    def __init__(
        self,
        *,
        config: HyperFramesToolConfig,
        bus: MessageBus | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        self.config = config
        self.bus = bus
        self.sessions = sessions

    @property
    def name(self) -> str:
        return "render_video"

    @property
    def description(self) -> str:
        return (
            "将 HyperFrames 项目目录渲染为 MP4 视频。"
            "使用 `npx hyperframes render` 命令，需要 Chrome 浏览器。"
            "HTML 中的 data-start 和 data-duration 属性控制时间轴，"
            "支持 CSS 动画和 GSAP。需要 Node.js >= 20、FFmpeg 和 Chrome。"
        )

    async def execute(
        self,
        project_dir: str,
        output_path: str | None = None,
        fps: int | None = None,
        quality: str | None = None,
        output_format: str | None = None,
        **kwargs: Any,
    ) -> str:
        project_root = Path(self.config.project_root)
        proj_dir = Path(project_dir).expanduser()

        if not proj_dir.is_absolute():
            proj_dir = project_root / project_dir

        if not proj_dir.exists():
            return ToolResult.error(
                f"Error: Project directory not found: {proj_dir}"
            )

        index_html = proj_dir / "index.html"
        if not index_html.exists():
            return ToolResult.error(
                f"Error: index.html not found in project directory: {proj_dir}"
            )

        # Check dependencies
        node_bin = shutil.which("node")
        if not node_bin:
            return ToolResult.error(
                "Error: Node.js is not installed. Install Node.js >= 20."
            )

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return ToolResult.error(
                "Error: FFmpeg is not installed. Install ffmpeg."
            )

        # Resolve output path
        renders_dir = proj_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)

        use_fps = fps or self.config.default_fps
        use_quality = quality or self.config.default_quality
        use_format = output_format or self.config.default_format

        if output_path:
            out_file = Path(output_path)
            if not out_file.is_absolute():
                out_file = proj_dir / output_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            proj_name = proj_dir.name
            out_file = renders_dir / f"{proj_name}.{use_format}"

        # Build render command
        # --workers 1: limit to single Chrome process (~256MB) for low-memory servers
        # --no-browser-gpu: disable Chrome GPU accel to save memory
        extra_args = [
            str(proj_dir),
            "-o", str(out_file),
            "-f", str(use_fps),
            "-q", use_quality,
            "--format", use_format,
            "--workers", str(self.config.workers),
        ]
        if self.config.no_browser_gpu:
            extra_args.append("--no-browser-gpu")

        cmd = _build_cli_cmd("render", project_root=project_root, extra_args=extra_args)
        env = _build_env(self.config.proxy)
        timeout = self.config.render_timeout

        try:
            exit_code, stdout, stderr = await _run_cli(
                cmd, cwd=str(proj_dir), env=env,
                timeout=timeout, label="HyperFrames render",
            )
        except asyncio.TimeoutError:
            return ToolResult.error(
                f"Error: HyperFrames render timed out after {timeout} seconds."
            )
        except FileNotFoundError as exc:
            return ToolResult.error(f"Error: {exc}")

        if exit_code != 0:
            err_msg = stderr if stderr else stdout
            return ToolResult.error(
                f"Error: HyperFrames render failed (exit code {exit_code}).\n"
                f"stdout: {stdout[:2000]}\n"
                f"stderr: {err_msg[:2000]}"
            )

        if not out_file.exists():
            return ToolResult.error(
                f"Error: Render completed but output file not found: {out_file}"
            )

        file_size = out_file.stat().st_size
        logger.info(
            "HyperFrames render complete: {} ({} bytes)",
            out_file, file_size,
        )

        # Auto-deliver video to user via MessageBus
        delivered = ""
        if self.config.auto_deliver and self.bus:
            from nanobot.agent.tools.context import current_request_context
            from nanobot.bus.events import OutboundMessage

            ctx = current_request_context()
            if ctx:
                try:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=ctx.channel,
                        chat_id=ctx.chat_id,
                        content=(
                            f"✅ 视频渲染完成！\n"
                            f"**输出路径**: `{out_file}`\n"
                            f"**文件大小**: {file_size / 1024 / 1024:.1f} MB\n"
                            f"**帧率**: {use_fps} fps\n"
                            f"**质量**: {use_quality}"
                        ),
                        media=[str(out_file)],
                    ))
                    delivered = "\n视频已发送给用户。"
                except Exception as exc:
                    logger.warning("Failed to auto-deliver video: {}", exc)

        return (
            f"✅ 视频渲染完成！\n"
            f"**输出路径**: `{out_file}`\n"
            f"**文件大小**: {file_size / 1024 / 1024:.1f} MB\n"
            f"**帧率**: {use_fps} fps\n"
            f"**质量**: {use_quality}"
            f"{delivered}"
        )


# ---------------------------------------------------------------------------
# generate_tts_audio
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        required=["text"],
        text=StringSchema(
            "配音文本内容。支持中英文。",
            min_length=1,
        ),
        output_path=StringSchema(
            "输出音频路径。默认 audio/speech.wav。",
            nullable=True,
        ),
        voice=StringSchema(
            "Kokoro-82M 语音 ID。可选: "
            "zf_xiaobei (中文/小贝), "
            "af_heart (Heart), af_nova (Nova), af_sky (Sky), "
            "am_adam (Adam), am_michael (Michael), "
            "bf_emma (Emma), bf_isabella (Isabella), "
            "bm_george (George), jf_alpha (Alpha/日语)。"
            "默认 zf_xiaobei。",
            nullable=True,
        ),
        speed=StringSchema(
            "语速倍数，如 1.0 (正常), 1.2 (加快), 0.8 (减慢)。默认 1.0。",
            nullable=True,
        ),
        lang=StringSchema(
            "语音语言: en-us, en-gb, es, fr-fr, hi, it, pt-br, ja, zh。"
            "不传时根据语音前缀自动检测。",
            nullable=True,
        ),
    )
)
class GenerateTtsAudioTool(Tool):
    """Generate TTS audio from text using HyperFrames built-in Kokoro-82M model."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(config=ctx.config.hyperframes)

    def __init__(self, *, config: HyperFramesToolConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "generate_tts_audio"

    @property
    def description(self) -> str:
        return (
            "将文本转换为语音（TTS），生成 WAV 音频文件。"
            "使用 HyperFrames 内置的 Kokoro-82M 本地 AI 语音模型，无需网络。"
            "支持中文语音 zf_xiaobei。适合为视频添加旁白/配音。"
        )

    async def execute(
        self,
        text: str,
        output_path: str | None = None,
        voice: str | None = None,
        speed: str | None = None,
        lang: str | None = None,
        **kwargs: Any,
    ) -> str:
        project_root = Path(self.config.project_root)
        audio_dir = project_root / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        if output_path:
            out_file = Path(output_path)
            if not out_file.is_absolute():
                out_file = project_root / output_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_file = audio_dir / "speech.wav"

        use_voice = voice or self.config.default_voice
        use_speed = float(speed) if speed else self.config.default_speed

        # Resolve Kokoro model paths (cached by HyperFrames CLI)
        cache_dir = Path.home() / ".cache" / "hyperframes" / "tts"
        model_path = cache_dir / "models" / "kokoro-v1.0.onnx"
        voices_path = cache_dir / "voices" / "voices-v1.0.bin"

        if not model_path.exists() or not voices_path.exists():
            return ToolResult.error(
                "Error: Kokoro TTS model not found. Run `hyperframes tts` CLI "
                "once to download the model, or manually download:\n"
                f"  {model_path}\n"
                f"  {voices_path}"
            )

        # Use Python kokoro-onnx library directly (bypasses CLI phonemizer zh bug)
        timeout = self.config.tts_timeout
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._generate_tts_sync,
                    str(model_path),
                    str(voices_path),
                    text,
                    use_voice,
                    use_speed,
                    str(out_file),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult.error(
                f"Error: TTS generation timed out after {timeout} seconds."
            )
        except ImportError:
            return ToolResult.error(
                "Error: kokoro-onnx package not installed. "
                "Install with: pip install kokoro-onnx soundfile"
            )
        except Exception as exc:
            return ToolResult.error(f"Error: TTS failed: {exc}")

        if not out_file.exists():
            return ToolResult.error(
                f"Error: TTS completed but output file not found: {out_file}"
            )

        file_size = out_file.stat().st_size
        logger.info("TTS audio generated: {} ({} bytes)", out_file, file_size)

        return (
            f"✅ 配音音频生成完成！\n"
            f"**输出路径**: `{out_file}`\n"
            f"**文件大小**: {file_size / 1024:.1f} KB\n"
            f"**语音**: {use_voice}\n"
            f"**语速**: {use_speed}x"
            f"{result}"
        )

    @staticmethod
    def _generate_tts_sync(
        model_path: str,
        voices_path: str,
        text: str,
        voice: str,
        speed: float,
        output_file: str,
    ) -> str:
        """Run Kokoro TTS synchronously (called in thread executor)."""
        from kokoro_onnx import Kokoro
        import soundfile as sf

        k = Kokoro(model_path, voices_path)
        audio, sr = k.create(text, voice=voice, speed=speed)
        sf.write(output_file, audio, sr)
        duration = len(audio) / sr
        return f"\n**时长**: {duration:.1f}s\n**采样率**: {sr}Hz"


# ---------------------------------------------------------------------------
# merge_video_audio
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        required=["video_path", "audio_path"],
        video_path=StringSchema(
            "视频文件路径（MP4/WebM/MOV）。",
            min_length=1,
        ),
        audio_path=StringSchema(
            "音频文件路径（WAV/MP3/M4A）。",
            min_length=1,
        ),
        output_path=StringSchema(
            "合并后输出路径。默认 output/final.mp4",
            nullable=True,
        ),
    )
)
class MergeVideoAudioTool(Tool):
    """Merge a video file and an audio file using FFmpeg."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(
            config=ctx.config.hyperframes,
            bus=ctx.bus,
        )

    def __init__(
        self,
        *,
        config: HyperFramesToolConfig,
        bus: MessageBus | None = None,
    ) -> None:
        self.config = config
        self.bus = bus

    @property
    def name(self) -> str:
        return "merge_video_audio"

    @property
    def description(self) -> str:
        return (
            "使用 FFmpeg 合并视频和音频文件。"
            "将 MP4 视频和 WAV/MP3 音频合并为带配音的最终视频。"
            "使用 -shortest 选项，输出时长以较短的流为准。"
        )

    async def execute(
        self,
        video_path: str,
        audio_path: str,
        output_path: str | None = None,
        **kwargs: Any,
    ) -> str:
        project_root = Path(self.config.project_root)

        video_file = Path(video_path).expanduser()
        audio_file = Path(audio_path).expanduser()

        if not video_file.is_absolute():
            video_file = project_root / video_path
        if not audio_file.is_absolute():
            audio_file = project_root / audio_path

        if not video_file.exists():
            return ToolResult.error(f"Error: Video file not found: {video_file}")
        if not audio_file.exists():
            return ToolResult.error(f"Error: Audio file not found: {audio_file}")

        # Resolve output path
        out_dir = project_root / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        if output_path:
            out_file = Path(output_path)
            if not out_file.is_absolute():
                out_file = project_root / output_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_file = out_dir / "final.mp4"

        # Check ffmpeg
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return ToolResult.error("Error: FFmpeg is not installed.")

        # Build ffmpeg command: combine video + audio
        cmd = [
            ffmpeg_bin,
            "-y",  # overwrite output
            "-i", str(video_file),
            "-i", str(audio_file),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(out_file),
        ]

        timeout = self.config.merge_timeout

        try:
            logger.info("Running ffmpeg merge: {}", " ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace") if stderr else ""
                return ToolResult.error(
                    f"Error: FFmpeg merge failed (exit code {proc.returncode}).\n"
                    f"stderr: {err_msg[:2000]}"
                )

            if not out_file.exists():
                return ToolResult.error(
                    f"Error: Merge completed but output file not found: {out_file}"
                )

            file_size = out_file.stat().st_size
            logger.info(
                "Video+audio merge complete: {} ({} bytes)",
                out_file, file_size,
            )

            # Auto-deliver final video to user
            delivered = ""
            if self.config.auto_deliver and self.bus:
                from nanobot.agent.tools.context import current_request_context
                from nanobot.bus.events import OutboundMessage

                ctx = current_request_context()
                if ctx:
                    try:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=ctx.channel,
                            chat_id=ctx.chat_id,
                            content=(
                                f"✅ 视频+配音合并完成！\n"
                                f"**输出路径**: `{out_file}`\n"
                                f"**文件大小**: {file_size / 1024 / 1024:.1f} MB"
                            ),
                            media=[str(out_file)],
                        ))
                        delivered = "\n最终视频已发送给用户。"
                    except Exception as exc:
                        logger.warning("Failed to auto-deliver merged video: {}", exc)

            return (
                f"✅ 视频+配音合并完成！\n"
                f"**输出路径**: `{out_file}`\n"
                f"**文件大小**: {file_size / 1024 / 1024:.1f} MB"
                f"{delivered}"
            )

        except asyncio.TimeoutError:
            return ToolResult.error(
                f"Error: FFmpeg merge timed out after {timeout} seconds."
            )
        except FileNotFoundError as exc:
            return ToolResult.error(f"Error: Command not found: {exc}")
        except Exception as exc:
            return ToolResult.error(f"Error: Merge failed: {exc}")


# ---------------------------------------------------------------------------
# lint_composition
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        required=["project_dir"],
        project_dir=StringSchema(
            "HyperFrames 项目目录路径。",
            min_length=1,
        ),
    )
)
class LintCompositionTool(Tool):
    """Validate a HyperFrames composition for common mistakes."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(config=ctx.config.hyperframes)

    def __init__(self, *, config: HyperFramesToolConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "lint_composition"

    @property
    def description(self) -> str:
        return (
            "验证 HyperFrames 项目组合是否有常见错误。"
            "在渲染前运行 lint 检查可以提前发现问题。"
            "返回验证结果和警告信息。"
        )

    async def execute(
        self,
        project_dir: str,
        **kwargs: Any,
    ) -> str:
        project_root = Path(self.config.project_root)
        proj_dir = Path(project_dir).expanduser()

        if not proj_dir.is_absolute():
            proj_dir = project_root / project_dir

        if not proj_dir.exists():
            return ToolResult.error(
                f"Error: Project directory not found: {proj_dir}"
            )

        node_bin = shutil.which("node")
        if not node_bin:
            return ToolResult.error("Error: Node.js is not installed.")

        cmd = _build_cli_cmd(
            "lint",
            project_root=project_root,
            extra_args=[str(proj_dir), "--verbose"],
        )
        env = _build_env(self.config.proxy)

        try:
            exit_code, stdout, stderr = await _run_cli(
                cmd, cwd=str(proj_dir), env=env,
                timeout=60, label="HyperFrames lint",
            )
        except asyncio.TimeoutError:
            return ToolResult.error("Error: Lint timed out after 60 seconds.")
        except FileNotFoundError as exc:
            return ToolResult.error(f"Error: {exc}")

        output = stdout or stderr
        if exit_code == 0:
            return (
                f"✅ 组合验证通过！\n"
                f"**项目**: `{proj_dir}`\n"
                f"{output[:3000]}"
            )
        else:
            return (
                f"⚠️ 组合验证发现问题：\n"
                f"**项目**: `{proj_dir}`\n"
                f"{output[:3000]}"
            )


# ---------------------------------------------------------------------------
# list_compositions
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema()
)
class ListCompositionsTool(Tool):
    """List all HyperFrames compositions in the project root."""

    config_key = "hyperframes"
    _scopes = {"core", "subagent"}
    _plugin_discoverable = True

    @classmethod
    def config_cls(cls):
        return HyperFramesToolConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.hyperframes.enabled

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(config=ctx.config.hyperframes)

    def __init__(self, *, config: HyperFramesToolConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "list_compositions"

    @property
    def description(self) -> str:
        return "列出 HyperFrames 项目根目录下所有已创建的合成项目。"

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        project_root = Path(self.config.project_root)
        compositions_dir = project_root / "compositions"

        if not compositions_dir.exists():
            return f"合成目录不存在: {compositions_dir}"

        compositions = []
        for item in sorted(compositions_dir.iterdir()):
            if item.is_dir() and (item / "index.html").exists():
                # Try to read meta.json for display name
                meta_file = item / "meta.json"
                display_name = item.name
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                        display_name = meta.get("name", item.name)
                    except Exception:
                        pass
                compositions.append({
                    "name": item.name,
                    "display_name": display_name,
                    "path": str(item),
                })

        if not compositions:
            return f"没有找到合成项目。使用 create_composition 创建新项目。\n目录: {compositions_dir}"

        lines = [f"找到 {len(compositions)} 个合成项目：\n"]
        for comp in compositions:
            lines.append(f"- **{comp['display_name']}** (`{comp['name']}`)")
            lines.append(f"  路径: `{comp['path']}`")
        return "\n".join(lines)
