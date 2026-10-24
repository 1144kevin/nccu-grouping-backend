import base64
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from PIL import Image
from pydantic import ValidationError

from app.api.routes import ai
from app.core_cloud.post_writing import WritingInput, draft_post, write_post

LONG_TEXT = (
    "活動剩餘便當，共 5 份，歡迎到正門一起領取！"
    "有興趣的朋友可以先留言討論，一起確認餐點內容與領取方式，"
    "實際安排請在參與前確認清楚，歡迎有需要的人一起加入。"
)


def provider(text):
    return SimpleNamespace(extract=lambda *_: json.dumps({"text": text}))


def test_draft_preserves_known_numbers_and_uses_structured_output():
    text = LONG_TEXT
    assert write_post(WritingInput(mode="generate", content=text), provider(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "每份 80 元！歡迎到正門領取便當。",
        "短稿",
        "無效字元\u0000不能出現在貼文內容裡",
        "無效字元\ud800不能出現在貼文內容裡",
    ],
)
def test_invalid_drafts_fail_without_returning_model_text(text):
    with pytest.raises(HTTPException) as caught:
        write_post(WritingInput(mode="polish", content="活動剩餘便當歡迎領取"), provider(text))
    assert caught.value.detail == "post_writing_invalid_output"


def test_route_local_only_quota_and_rechecks_session(monkeypatch):
    user = SimpleNamespace(id="synthetic-actor")
    config = SimpleNamespace(
        environment="local",
        core_cloud_enabled=False,
        stealth_ai_provider="ollama_local",
        stealth_ollama_local_only_confirmed=True,
        stealth_ollama_model="synthetic-local",
    )
    request = SimpleNamespace(state=SimpleNamespace(runtime_settings=config))
    calls = []
    monkeypatch.setattr(
        ai,
        "OllamaDraftProvider",
        lambda *_args, **_kwargs: (
            calls.append("inference") or provider(LONG_TEXT.replace(" 5 ", "幾"))
        ),
    )
    store = SimpleNamespace(authenticate=lambda token: calls.append(token) or user)
    ai.quota.entries.clear()
    value = WritingInput(mode="generate", content="活動剩餘便當歡迎領取")
    assert ai.post_writing(value, request, Response(), store, user, "synthetic-token").text
    assert calls == ["inference", "synthetic-token"]
    store.authenticate = lambda _: SimpleNamespace(id="different-actor")
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 401
    config.core_cloud_enabled = True
    before = len(calls)
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 503 and len(calls) == before
    config.core_cloud_enabled = False
    ai.quota.entries.clear()
    for _ in range(6):
        assert ai.quota.allow(("post-writing", str(user.id)), 6)
    with pytest.raises(HTTPException) as caught:
        ai.post_writing(value, request, Response(), store, user, "synthetic-token")
    assert caught.value.status_code == 429
    ai.quota.entries.clear()


def picture():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "yellow").save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode()


def test_image_is_decoded_and_recognized_before_writing_without_uploading():
    calls = []
    observed = "披薩買一送一，限外帶，活動到10月31日。"
    text = (
        "看到披薩買一送一的外帶活動，想找也想吃披薩的朋友一起討論！"
        "宣傳單標示活動到10月31日，實際適用品項與優惠限制請先向店家確認，"
        "有興趣可以留言協調餐點、費用與取餐安排，再決定是否一起參加。"
    )

    def extract(messages, schema, prompt):
        calls.append(messages)
        return json.dumps({"text": observed if len(calls) == 1 else text})

    result = draft_post(
        WritingInput(mode="image", image_base64=picture()), SimpleNamespace(extract=extract)
    )
    assert result.text == text and result.source_text == observed
    assert len(calls) == 2
    assert base64.b64decode(calls[0][0]["images"][0]).startswith(b"\xff\xd8")
    assert observed in calls[1][0]["content"]
    assert "images" not in calls[1][0] and "image_base64" not in calls[1][0]["content"]


@pytest.mark.parametrize("image", ["not_base64", "YQ=="])
def test_invalid_images_are_rejected_before_model_call(image):
    def never(*_):
        raise AssertionError("must not infer")

    with pytest.raises(HTTPException):
        draft_post(WritingInput(mode="image", image_base64=image), SimpleNamespace(extract=never))


def test_unreadable_image_does_not_invent_an_offer():
    with pytest.raises(HTTPException) as caught:
        draft_post(WritingInput(mode="image", image_base64=picture()), provider(""))
    assert caught.value.detail == "post_image_unreadable"


def test_voice_keeps_transcript_and_does_not_accept_short_or_invented_prices():
    result = draft_post(WritingInput(mode="voice", content=LONG_TEXT), provider(LONG_TEXT))
    assert result.source_text == LONG_TEXT and len(result.text) >= 50
    for output in ("想揪人一起吃披薩，歡迎參加。", "空白" + " " * 60, LONG_TEXT + "每份80元"):
        with pytest.raises(HTTPException):
            draft_post(WritingInput(mode="voice", content=LONG_TEXT), provider(output))


def test_generated_text_must_not_change_a_per_item_price_to_per_slice():
    source = "大披薩每個原價399元，買一送一，限外帶。"
    output = "大披薩每片原價399元，買一送一，限外帶。" + LONG_TEXT.replace(" 5 ", "幾")
    with pytest.raises(HTTPException) as caught:
        write_post(WritingInput(mode="generate", content=source), provider(output))
    assert caught.value.detail == "post_writing_invalid_output"


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "voice", "content": "   "},
        {"mode": "image", "content": "照片"},
        {"mode": "voice", "content": "口述", "image_base64": "YQ=="},
    ],
)
def test_mode_requires_the_correct_input(value):
    with pytest.raises(ValidationError):
        WritingInput(**value)


def test_multi_photo_recognition_needs_no_typed_text_and_sends_the_entire_album():
    calls = []
    observed = "照片一是披薩優惠，照片二是飲料，照片三是品牌標誌。"

    def extract(messages, schema, prompt):
        calls.append(messages)
        return json.dumps({"text": observed if len(calls) == 1 else LONG_TEXT.replace(" 5 ", "幾")})

    result = draft_post(
        WritingInput(mode="image", images_base64=[picture()] * 3), SimpleNamespace(extract=extract)
    )
    assert len(calls[0][0]["images"]) == 3
    assert result.source_text == observed
    assert "images_base64" not in calls[1][0]["content"]


def test_invalid_writer_is_repaired_once_and_keeps_the_recognized_facts_on_failure():
    calls = []
    facts = "杯子品牌標誌，沒有價格或優惠。"

    def extract(messages, schema, prompt):
        calls.append(prompt)
        return json.dumps({"text": facts if len(calls) == 1 else "每人99元！"})

    result = draft_post(
        WritingInput(mode="image", images_base64=[picture()]), SimpleNamespace(extract=extract)
    )
    assert len(calls) == 3
    assert result.draft_method == "recognized_facts"
    assert facts in result.text and "99" not in result.text
    assert len(result.text) >= 50


def test_repaired_writer_can_recover_without_using_the_facts_template():
    replies = iter(["太短", LONG_TEXT])
    result = write_post(
        WritingInput(mode="generate", content=LONG_TEXT),
        SimpleNamespace(extract=lambda *_: json.dumps({"text": next(replies)})),
    )
    assert result == LONG_TEXT


@pytest.mark.parametrize("images", [[], [picture()] * 6, ["x" * 5_592_409]])
def test_album_is_bounded_before_inference(images):
    with pytest.raises(ValidationError):
        WritingInput(mode="image", images_base64=images)
