"""Video generation provider — Agnes Video V2.0 async API."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx
from loguru import logger

from nanobot.config.schema import Config, ProviderConfig

# ---------------------------------------------------------------------------
# Provider registry (matches the image_generation pattern)
# ---------------------------------------------------------------------------

_VIDEO_GEN_PROVIDERS: dict[str, type[VideoGenerationProvider]] = {}


def register_video_gen_provider(cls: type[VideoGenerationProvider]) -> type[VideoGenerationProvider]:
    name = cls.provider_name
    if not name:
        raise ValueError(f"{cls.__name__} must set provider_name")
    _VIDEO_GEN_PROVIDERS[name] = cls
    return cls


def get_video_gen_provider(name: str) -> type[VideoGenerationProvider] | None:
    return _VIDEO_GEN_PROVIDERS.get(name)


def video_gen_provider_configs(config: Config) -> dict[str, ProviderConfig]:
    """Return all configured video-generation provider configs."""
    result: dict[str, ProviderConfig] = {}
    for name in _VIDEO_GEN_PROVIDERS:
        pc = getattr(config.providers, name, None)
        if pc is not None:
            result[name] = pc
    return result


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIDEO_POLL_INTERVAL_S = 10.0       # 轮询间隔
_VIDEO_POLL_MAX_DURATION_S = 300.0  # 5 分钟超时
_VIDEO_429_RETRY_MAX = 3            # 1 initial + 2 retries
_VIDEO_429_BACKOFF_BASE = 2.0      # seconds: 2, 4, 8

# 429 classification — mirrors LLMProvider._NON_RETRYABLE_429_TEXT_MARKERS
_NON_RETRYABLE_429_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota exhausted",
    "billing hard limit",
    "billing_hard_limit_reached",
    "billing not active",
    "billing_not_active",
    "insufficient balance",
    "insufficient_balance",
    "credit balance too low",
    "credit_balance_too_low",
    "payment required",
    "payment_required",
    "out of credits",
    "out of quota",
    "exceeded your current quota",
)


# ---------------------------------------------------------------------------
# Response / error types
# ---------------------------------------------------------------------------

@dataclass
class VideoTaskResponse:
    """Response from creating or querying a video task."""

    video_id: str
    task_id: str
    status: str           # queued | in_progress | completed | failed
    progress: int         # 0-100
    seconds: str | None    # 视频时长
    size: str | None       # 分辨率
    created_at: int | None
    video_url: str | None = None      # 完成后的视频下载 URL
    error: str | None = None


class VideoGenerationError(Exception):
    """Video generation error.

    The ``kind`` attribute classifies the error so callers can decide
    whether to retry (rate_limit), give up immediately (quota_exhausted),
    or treat as unexpected (unknown).
    """

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


# Status values the API may return that mean "completed".
_COMPLETED_STATUSES = frozenset({
    "completed", "complete", "succeed", "success", "done", "finished",
})


def _normalize_status(raw: str) -> str:
    """Normalise various API status strings to canonical values.

    Maps synonyms like ``succeed``, ``success``, ``done`` → ``completed``.
    """
    s = raw.lower().strip()
    if s in _COMPLETED_STATUSES:
        return "completed"
    return s


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class VideoGenerationProvider:
    """Base class for video generation providers."""

    provider_name: str = ""
    missing_key_message: str = ""
    default_timeout: float = 120.0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or ""
        raw_base = api_base or self._default_base_url()
        # Strip trailing /v1 (or /v2, etc.) since we always append /v1/videos
        raw_base = raw_base.rstrip("/")
        if re.search(r"/v\d+$", raw_base):
            raw_base = re.sub(r"/v\d+$", "", raw_base)
        self.api_base = raw_base
        self.extra_headers = dict(extra_headers or {})
        self.extra_body = dict(extra_body or {})
        self.proxy = proxy
        self.timeout = timeout if timeout is not None else self.default_timeout
        self._client = client

    def _default_base_url(self) -> str:
        return ""

    def _http_client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
            kwargs["trust_env"] = False
        return kwargs

    # -- 429 retry helpers --------------------------------------------------

    @staticmethod
    def _is_retryable_429(response: httpx.Response) -> bool:
        """Determine if a 429 is retryable (rate limit) vs non-retryable (quota)."""
        if response.status_code != 429:
            return False
        text = response.text.lower()
        return not any(marker in text for marker in _NON_RETRYABLE_429_MARKERS)

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> float:
        """Extract Retry-After header value in seconds."""
        retry_after = response.headers.get("retry-after")
        if not retry_after:
            return _VIDEO_429_BACKOFF_BASE
        retry_after = retry_after.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", retry_after):
            return float(retry_after)
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.1, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds())
        except Exception:
            return _VIDEO_429_BACKOFF_BASE

    # -- Abstract methods --------------------------------------------------

    async def create_task(
        self,
        *,
        model: str,
        prompt: str,
        image: str | list[str] | None = None,
        mode: str | None = None,
        width: int | None = None,
        height: int | None = None,
        num_frames: int | None = None,
        frame_rate: int | None = None,
        num_inference_steps: int | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> VideoTaskResponse:
        raise NotImplementedError

    async def get_task_status(self, video_id: str) -> VideoTaskResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Agnes Video V2.0 client
# ---------------------------------------------------------------------------

@register_video_gen_provider
class AgnesVideoGenerationClient(VideoGenerationProvider):
    """Agnes Video V2.0 async video generation client."""

    provider_name = "agnes"
    missing_key_message = (
        "Agnes AI API key is not configured. "
        "Set providers.agnes.apiKey in config."
    )

    def _default_base_url(self) -> str:
        return "https://apihub.agnes-ai.com"

    async def create_task(
        self,
        *,
        model: str,
        prompt: str,
        image: str | list[str] | None = None,
        mode: str | None = None,
        width: int | None = None,
        height: int | None = None,
        num_frames: int | None = None,
        frame_rate: int | None = None,
        num_inference_steps: int | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> VideoTaskResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        body: dict[str, Any] = {"model": model, "prompt": prompt}
        if image:
            body["image"] = image
        if mode:
            body["mode"] = mode
        if width is not None:
            body["width"] = width
        if height is not None:
            body["height"] = height
        if num_frames is not None:
            body["num_frames"] = num_frames
        if frame_rate is not None:
            body["frame_rate"] = frame_rate
        if num_inference_steps is not None:
            body["num_inference_steps"] = num_inference_steps
        if seed is not None:
            body["seed"] = seed
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if extra_body:
            body.update(extra_body)
        body.update(self.extra_body)

        url = f"{self.api_base}/v1/videos"

        for attempt in range(_VIDEO_429_RETRY_MAX):
            client = self._client or httpx.AsyncClient(**self._http_client_kwargs())
            try:
                response = await client.post(url, headers=headers, json=body)
                if response.status_code == 429:
                    if self._is_retryable_429(response):
                        if attempt < _VIDEO_429_RETRY_MAX - 1:
                            delay = self._extract_retry_after(response)
                            logger.warning(
                                "Agnes video create got 429 (attempt {}/{}), "
                                "retrying in {:.1f}s",
                                attempt + 1, _VIDEO_429_RETRY_MAX, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise VideoGenerationError(
                            f"Agnes video API rate limited: {response.text[:500]}",
                            kind="rate_limit",
                        )
                    raise VideoGenerationError(
                        f"Agnes video API quota exceeded: {response.text[:500]}",
                        kind="quota_exhausted",
                    )
                response.raise_for_status()
                data = response.json()
                return VideoTaskResponse(
                    video_id=data.get("video_id", ""),
                    task_id=data.get("task_id", data.get("id", "")),
                    status=_normalize_status(data.get("status", "queued")),
                    progress=data.get("progress", 0),
                    seconds=str(data.get("seconds", "")) if data.get("seconds") else None,
                    size=data.get("size"),
                    created_at=data.get("created_at"),
                )
            except httpx.TimeoutException:
                raise VideoGenerationError(
                    "Agnes video task creation timed out", kind="unknown",
                )
            except httpx.RequestError as exc:
                raise VideoGenerationError(f"Request failed: {exc}", kind="unknown")
            finally:
                if self._client is None:
                    await client.aclose()

        raise VideoGenerationError(
            "Agnes video task creation failed after retries", kind="rate_limit",
        )

    async def get_task_status(self, video_id: str) -> VideoTaskResponse:
        if not self.api_key:
            raise VideoGenerationError(self.missing_key_message)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            **self.extra_headers,
        }
        url = f"{self.api_base}/agnesapi?video_id={video_id}"

        for attempt in range(_VIDEO_429_RETRY_MAX):
            client = self._client or httpx.AsyncClient(**self._http_client_kwargs())
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 429:
                    if self._is_retryable_429(response):
                        if attempt < _VIDEO_429_RETRY_MAX - 1:
                            delay = self._extract_retry_after(response)
                            logger.warning(
                                "Agnes video status got 429 (attempt {}/{}), "
                                "retrying in {:.1f}s",
                                attempt + 1, _VIDEO_429_RETRY_MAX, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                    raise VideoGenerationError(
                        f"Agnes video status API quota exceeded: {response.text[:500]}"
                    )
                response.raise_for_status()
                data: dict[str, Any] = response.json()

                logger.debug(
                    "Agnes video status response for {}: status={} progress={} has_url={}",
                    video_id,
                    data.get("status"),
                    data.get("progress", 0),
                    bool(
                        isinstance(data.get("metadata"), dict)
                        and data.get("metadata", {}).get("url")
                    ),
                )

                video_url: str | None = None
                # Try multiple possible locations for the video URL
                metadata = data.get("metadata")
                if isinstance(metadata, dict):
                    meta = cast(dict[str, Any], metadata)
                    url_val: object = meta.get("url")
                    if isinstance(url_val, str):
                        video_url = url_val
                if not video_url:
                    # Fallback: check top-level fields
                    for key in ("url", "video_url", "download_url", "result_url"):
                        val = data.get(key)
                        if isinstance(val, str) and val.startswith("http"):
                            video_url = val
                            break
                if not video_url:
                    # Fallback: check nested result object
                    result_obj = data.get("result")
                    if isinstance(result_obj, dict):
                        for key in ("url", "video_url", "download_url"):
                            val = cast(dict[str, Any], result_obj).get(key)
                            if isinstance(val, str) and val.startswith("http"):
                                video_url = val
                                break

                error_msg: str | None = None
                error = data.get("error")
                if isinstance(error, dict):
                    err = cast(dict[str, Any], error)
                    msg_val: object = err.get("message")
                    if isinstance(msg_val, str):
                        error_msg = msg_val
                elif isinstance(error, str):
                    error_msg = error

                return VideoTaskResponse(
                    video_id=data.get("video_id", video_id),
                    task_id=data.get("task_id", data.get("id", "")),
                    status=_normalize_status(data.get("status", "unknown")),
                    progress=data.get("progress", 0),
                    seconds=str(data.get("seconds", "")) if data.get("seconds") else None,
                    size=data.get("size"),
                    created_at=data.get("created_at"),
                    video_url=video_url,
                    error=error_msg,
                )
            except httpx.TimeoutException:
                raise VideoGenerationError("Agnes video status query timed out")
            except httpx.RequestError as exc:
                raise VideoGenerationError(f"Status query failed: {exc}")
            finally:
                if self._client is None:
                    await client.aclose()

        raise VideoGenerationError("Agnes video status query failed after retries")
