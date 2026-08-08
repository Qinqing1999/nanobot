"""Tests for the media-only message guard in the agent runner."""

from nanobot.agent.runner import _extract_last_user_text, _is_media_only_message


def test_wechat_image_only_message_detected() -> None:
    text = "[image]\n[Image: source: /tmp/test.jpg]"
    assert _is_media_only_message(text)


def test_image_with_text_instruction_not_blocked() -> None:
    text = "[image]\n[Image: source: /tmp/test.jpg]\n这是什么？"
    assert not _is_media_only_message(text)


def test_empty_text_detected_as_media_only() -> None:
    assert _is_media_only_message("")
    assert _is_media_only_message("   ")


def test_pure_text_message_not_blocked() -> None:
    text = "帮我生成一张猫的图片"
    assert not _is_media_only_message(text)


def test_extract_last_user_text_from_messages() -> None:
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "[image]\n[Image: source: /tmp/test.jpg]"},
    ]
    text = _extract_last_user_text(messages)
    assert "[image]" in text


def test_multimodal_message_with_image_only_text_detected() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[image]"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
        },
    ]
    text = _extract_last_user_text(messages)
    assert "[image]" in text
    assert _is_media_only_message(text)


def test_video_only_message_detected() -> None:
    text = "[video]\n[Video: source: /tmp/test.mp4]"
    assert _is_media_only_message(text)


def test_voice_only_message_detected() -> None:
    # Voice without transcription (only audio reference)
    text = "[voice]\n[Audio: source: /tmp/test.wav]"
    assert _is_media_only_message(text)


def test_voice_with_transcription_not_blocked() -> None:
    # Voice with transcribed text contains meaningful user instruction
    text = "[voice] please describe this image"
    assert not _is_media_only_message(text)


def test_image_with_generate_instruction_not_blocked() -> None:
    text = "[image]\n[Image: source: /tmp/test.jpg]\n请根据这张图片生成一个视频"
    assert not _is_media_only_message(text)
