from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.routes.profile_media import PersonalMedia
from app.core.config import get_settings
from app.core_cloud.engagement import ReportInput, ReportView
from app.core_cloud.images import normalize_image
from app.core_cloud.media import AssetView, MediaService
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileInput, ProfileView
from app.main import app


def test_new_reports_require_evidence_but_legacy_reports_remain_readable():
    with pytest.raises(ValueError):
        ReportInput(kind="feedback", detail="這是足夠長度的問題描述，沒有附圖。")
    legacy = ReportView(
        kind="feedback",
        detail="這是既有回報，需要繼續可讀取。",
        id=uuid4(),
        status="received",
        created_at=datetime.now(UTC),
    )
    assert legacy.evidence_image_id is None


def test_avatar_input_is_optional_and_unset_does_not_erase_existing_photo():
    value = ProfileInput(display_name="測試", bio="", badge="🍱", expected_version=1)
    assert "avatar_id" not in value.model_dump(exclude_unset=True)


def test_personal_upload_scopes_rpc_and_resizes_avatar(monkeypatch):
    monkeypatch.setenv("CORE_MEDIA_ENABLED", "true")
    monkeypatch.setenv("CORE_MEDIA_SIGNING_KEY", "a" * 64)
    get_settings.cache_clear()
    try:
        store = Mock(spec=SupabaseCoreStore)
        store.token = "synthetic-session"
        service = PersonalMedia(store, uuid4())
        service.purpose = "avatar"
        service.rpc("reserve", {"width": 20})
        assert store._request.call_args.kwargs["body"]["args"]["purpose"] == "avatar"
        captured = []
        monkeypatch.setattr(
            MediaService,
            "upload",
            lambda _self, image, key: (
                captured.append(image)
                or AssetView(id=uuid4(), width=image.width, height=image.height, state="ready")
            ),
        )
        output = BytesIO()
        Image.new("RGB", (1600, 900)).save(output, format="JPEG")
        result = service.upload(normalize_image(output.getvalue()), "synthetic-image-key")
        assert result.width == 320 and result.height == 180 and len(captured[0].content) < 30_000
    finally:
        get_settings.cache_clear()


def test_likes_use_current_session_and_reject_forged_actor(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-session"
    store.authenticate.return_value = ProfileView(
        id=uuid4(),
        email="synthetic@example.test",
        display_name="測試",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    store._request.return_value = {"likes": 1, "liked": True}
    app.dependency_overrides[dependencies.get_store] = lambda: store
    target = uuid4()
    path = f"/api/v1/users/{target}/likes"
    headers = {"Authorization": "Bearer synthetic-session"}
    try:
        with TestClient(app) as client:
            assert client.get(path).status_code == 401
            assert (
                client.put(
                    path, headers=headers, json={"liked": True, "actor_id": str(uuid4())}
                ).status_code
                == 422
            )
            assert client.put(path, headers=headers, json={"liked": "true"}).status_code == 422
            assert client.put(path, headers=headers, json={"liked": True}).json() == {
                "likes": 1,
                "liked": True,
            }
            assert store._request.call_args.kwargs["body"] == {
                "action": "like",
                "args": {"user_id": str(target), "liked": True},
            }
            assert store._request.call_args.kwargs["token"] == "synthetic-session"
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        get_settings.cache_clear()
