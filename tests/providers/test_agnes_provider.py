"""Tests for the Agnes AI provider registration and image generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from nanobot.config.schema import ProvidersConfig
from nanobot.providers.image_generation import (
    _RETRY_MAX_ATTEMPTS,
    _RETRY_STATUS_CODES,
    AgnesImageGenerationClient,
    ImageGenerationError,
)
from nanobot.providers.registry import PROVIDERS, find_by_name

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdacd\xfc\xff\x1f\x00\x03\x03"
    b"\x02\x00\xef\xbf\xa7\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
RAW_B64 = PNG_DATA_URL.removeprefix("data:image/png;base64,")

# Agnes AI returns data[].url (hosted PNG) — not b64_json
IMAGE_URL = "https://platform-outputs.agnes-ai.space/images/t2i/test123.png"
URL_RESPONSE: dict[str, Any] = {
    "created": 1786086234,
    "data": [{"b64_json": None, "url": IMAGE_URL}],
}
# Some responses may include b64_json directly
B64_RESPONSE: dict[str, Any] = {
    "data": [{"b64_json": RAW_B64}],
}
# Response with no images
NO_IMAGE_RESPONSE: dict[str, Any] = {
    "data": [{"b64_json": None, "url": None}],
}


# ---------------------------------------------------------------------------
# Registry / config tests
# ---------------------------------------------------------------------------


def test_agnes_config_field_exists():
    """ProvidersConfig should have an agnes field."""
    config = ProvidersConfig()
    assert hasattr(config, "agnes")


def test_agnes_provider_in_registry():
    """Agnes AI should be registered in the provider registry."""
    specs = {s.name: s for s in PROVIDERS}
    assert "agnes" in specs

    agnes = specs["agnes"]
    assert agnes.backend == "openai_compat"
    assert agnes.env_key == "AGNES_API_KEY"
    assert agnes.default_api_base == "https://apihub.agnes-ai.com/v1"
    assert agnes.thinking_style == "enable_thinking"


def test_find_by_name_agnes():
    """find_by_name should resolve the Agnes AI provider."""
    spec = find_by_name("agnes")

    assert spec is not None
    assert spec.name == "agnes"
    assert spec.display_name == "Agnes AI"


def test_agnes_builtin_models():
    """Agnes AI should expose builtin model catalog."""
    spec = find_by_name("agnes")
    assert spec is not None

    model_ids = {m.id for m in spec.builtin_models}
    assert "agnes-2.0-flash" in model_ids
    assert "agnes-2.5-flash" in model_ids
    assert "agnes-2.5-pro-alpha" in model_ids

    flash = next(m for m in spec.builtin_models if m.id == "agnes-2.0-flash")
    assert flash.context_window == 524288


def test_agnes_image_generation_model_options():
    """AgnesImageGenerationClient should list agnes-image-2.0-flash as a model option."""
    assert "agnes-image-2.0-flash" in AgnesImageGenerationClient.model_options


# ---------------------------------------------------------------------------
# Retry constants tests
# ---------------------------------------------------------------------------


def test_retry_constants():
    """Verify retry constants are configured correctly."""
    assert _RETRY_MAX_ATTEMPTS == 3
    assert 429 in _RETRY_STATUS_CODES
    assert 503 in _RETRY_STATUS_CODES


# ---------------------------------------------------------------------------
# Image generation tests
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.request = httpx.Request("POST", "https://apihub.agnes-ai.com/v1/images/generations")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request, text=self.text)
            raise httpx.HTTPStatusError("failed", request=self.request, response=response)


class FakeClient:
    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(URL_RESPONSE)


@pytest.fixture(autouse=True)
def generated_image_downloads(monkeypatch) -> list[tuple[str, str | None]]:
    """Keep provider response parsing tests independent from outbound HTTP."""
    downloads: list[tuple[str, str | None]] = []

    async def download(url: str, *, proxy: str | None = None) -> str:
        downloads.append((url, proxy))
        return PNG_DATA_URL

    monkeypatch.setattr(
        "nanobot.providers.image_generation._download_image_data_url",
        download,
    )
    return downloads


@pytest.mark.asyncio
async def test_agnes_image_generation_payload_and_response(
    generated_image_downloads: list[tuple[str, str | None]],
) -> None:
    """Verify the Images API request body and URL response parsing."""
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        extra_headers={"X-Test": "1"},
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(
        prompt="a cat on the moon",
        model="agnes-image-2.0-flash",
        aspect_ratio="16:9",
    )

    assert response.images == [PNG_DATA_URL]
    assert generated_image_downloads == [(IMAGE_URL, None)]
    call = fake.calls[0]
    assert call["url"] == "https://apihub.agnes-ai.com/v1/images/generations"
    assert call["headers"]["Authorization"] == "Bearer sk-agnes-test"
    assert call["headers"]["X-Test"] == "1"
    body = call["json"]
    assert body["model"] == "agnes-image-2.0-flash"
    assert body["prompt"] == "a cat on the moon"
    assert body["n"] == 1
    assert body["size"] == "1536x1024"
    # response_format must NOT be sent (Agnes API rejects it)
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_agnes_image_generation_default_size() -> None:
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="a dog", model="agnes-image-2.0-flash")

    body = fake.calls[0]["json"]
    assert body["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_agnes_image_generation_explicit_size() -> None:
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="a bird",
        model="agnes-image-2.0-flash",
        image_size="1024x1536",
    )

    body = fake.calls[0]["json"]
    assert body["size"] == "1024x1536"


@pytest.mark.asyncio
async def test_agnes_image_generation_with_reference_image(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG_BYTES)
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="edit this image",
        model="agnes-image-2.0-flash",
        reference_images=[str(ref)],
    )

    body = fake.calls[0]["json"]
    assert "image" in body
    assert isinstance(body["image"], str)
    assert body["image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_agnes_image_generation_requires_api_key() -> None:
    client = AgnesImageGenerationClient(api_key=None)

    with pytest.raises(ImageGenerationError, match="API key"):
        await client.generate(prompt="draw", model="agnes-image-2.0-flash")


@pytest.mark.asyncio
async def test_agnes_image_generation_no_images_raises() -> None:
    fake = FakeClient(FakeResponse(NO_IMAGE_RESPONSE))
    client = AgnesImageGenerationClient(api_key="sk-agnes-test", client=fake)  # type: ignore[arg-type]

    with pytest.raises(ImageGenerationError, match="returned no images"):
        await client.generate(prompt="draw", model="agnes-image-2.0-flash")


@pytest.mark.asyncio
async def test_agnes_image_generation_http_error() -> None:
    fake = FakeClient(FakeResponse({"error": "bad request"}, status_code=400))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(ImageGenerationError, match="HTTP 400"):
        await client.generate(prompt="draw", model="agnes-image-2.0-flash")


@pytest.mark.asyncio
async def test_agnes_image_generation_uses_default_base_url() -> None:
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="test", model="agnes-image-2.0-flash")

    assert fake.calls[0]["url"] == "https://apihub.agnes-ai.com/v1/images/generations"


@pytest.mark.asyncio
async def test_agnes_image_generation_all_aspect_ratios() -> None:
    """Verify all supported aspect ratios map to correct sizes."""
    fake = FakeClient(FakeResponse(URL_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    expected = {
        "1:1": "1024x1024",
        "16:9": "1536x1024",
        "9:16": "1024x1536",
        "3:4": "1024x1536",
        "4:3": "1536x1024",
    }

    for ratio, expected_size in expected.items():
        await client.generate(
            prompt=f"test {ratio}",
            model="agnes-image-2.0-flash",
            aspect_ratio=ratio,
        )
        body = fake.calls[-1]["json"]
        assert body["size"] == expected_size


@pytest.mark.asyncio
async def test_agnes_image_generation_b64_response() -> None:
    """When the API returns b64_json directly, it should be used without downloading."""
    fake = FakeClient(FakeResponse(B64_RESPONSE))
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="b64 test", model="agnes-image-2.0-flash")

    assert response.images == [PNG_DATA_URL]


# ---------------------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agnes_image_generation_retries_on_503(monkeypatch) -> None:
    """Should retry on HTTP 503 and succeed on a later attempt."""
    async def _no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    fake = FakeClient(
        FakeResponse({"error": "service unavailable"}, status_code=503),
        FakeResponse(URL_RESPONSE),
    )
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="retry test", model="agnes-image-2.0-flash")

    assert response.images == [PNG_DATA_URL]
    assert len(fake.calls) == 2  # 1 failed + 1 success


@pytest.mark.asyncio
async def test_agnes_image_generation_retries_on_429(monkeypatch) -> None:
    """Should retry on HTTP 429 and succeed on a later attempt."""
    async def _no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    fake = FakeClient(
        FakeResponse({"error": "rate limited"}, status_code=429),
        FakeResponse({"error": "rate limited"}, status_code=429),
        FakeResponse(URL_RESPONSE),
    )
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="retry test", model="agnes-image-2.0-flash")

    assert response.images == [PNG_DATA_URL]
    assert len(fake.calls) == 3  # 2 failed + 1 success = _RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_agnes_image_generation_retry_exhausted(monkeypatch) -> None:
    """Should fail after exhausting all retry attempts."""
    async def _no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    fake = FakeClient(
        FakeResponse({"error": "service unavailable"}, status_code=503),
        FakeResponse({"error": "service unavailable"}, status_code=503),
        FakeResponse({"error": "service unavailable"}, status_code=503),
    )
    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(ImageGenerationError, match="HTTP 503"):
        await client.generate(prompt="exhausted retry", model="agnes-image-2.0-flash")

    assert len(fake.calls) == _RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_agnes_image_generation_no_retry_on_400(monkeypatch) -> None:
    """Should NOT retry on HTTP 400 (client error, not rate/availability)."""
    call_count = 0

    class CountingClient:
        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            nonlocal call_count
            call_count += 1
            return FakeResponse({"error": "bad request"}, status_code=400)

    client = AgnesImageGenerationClient(
        api_key="sk-agnes-test",
        api_base="https://apihub.agnes-ai.com/v1",
        client=CountingClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(ImageGenerationError, match="HTTP 400"):
        await client.generate(prompt="no retry", model="agnes-image-2.0-flash")

    assert call_count == 1  # no retries
