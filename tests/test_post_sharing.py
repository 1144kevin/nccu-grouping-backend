from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core_cloud.post_sharing import PostSharingService
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileView
from app.main import app

TOKEN = "a" * 64


@pytest.fixture
def store():
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-session"
    store.key = "sb_publishable_synthetic"
    store.authenticate.return_value = ProfileView(
        id=uuid4(),
        email="fixture@example.test",
        display_name="主揪",
        created_at=datetime.now(UTC),
    )
    return store


def public_post():
    return dict(
        id=str(uuid4()),
        author_name="主揪",
        author_badge="🍱",
        author_level=1,
        content="晚餐一起訂便當",
        tags=["晚餐"],
        created_at=datetime.now(UTC).isoformat(),
        deadline_at=datetime.now(UTC).isoformat(),
        capacity=3,
        joined=1,
        status="open",
        price_per_person="85",
        location_name="正門",
        images=[],
    )


def test_guest_read_is_anonymous_and_projection_removes_account_fields(store):
    store._request.return_value = {
        **public_post(),
        "email": "private@example.test",
        "members": ["private"],
    }
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            result = client.get(f"/api/v1/public/posts/{TOKEN}")
            assert result.status_code == 200
            assert "email" not in result.json() and "members" not in result.json()
            assert result.headers["cache-control"] == "no-store"
            assert store._request.call_args.kwargs["token"] is None
            store.authenticate.assert_not_called()
            assert client.post(f"/api/v1/posts/{uuid4()}/share").status_code == 401
            assert client.get("/api/v1/public/posts/malformed").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_share_creation_requires_user_and_passes_live_session(store):
    store._request.return_value = {"token": TOKEN}
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            result = client.post(
                f"/api/v1/posts/{uuid4()}/share",
                headers={"Authorization": "Bearer synthetic-session"},
            )
            assert result.status_code == 200 and result.json() == {"token": TOKEN}
            assert store._request.call_args.kwargs["token"] == "synthetic-session"
    finally:
        app.dependency_overrides.clear()


def test_public_image_uses_only_share_capability_and_bounded_bytes(store):
    owner, image = uuid4(), uuid4()
    store._request.return_value = {"object_path": f"{owner}/{image}.jpg"}

    def handler(request):
        assert request.headers["x-post-share"] == TOKEN
        assert "authorization" not in request.headers
        assert request.url.path == f"/storage/v1/object/core-post-images/{owner}/{image}.jpg"
        return httpx.Response(200, content=b"jpeg-fixture", headers={"Content-Type": "image/jpeg"})

    service = PostSharingService(store, transport=httpx.MockTransport(handler))
    assert service.image(TOKEN, image) == b"jpeg-fixture"
    assert store._request.call_args.kwargs["body"]["args"] == {
        "token": TOKEN,
        "image_id": str(image),
    }
    store._request.return_value = {"object_path": "https://untrusted.example/test.jpg"}
    with pytest.raises(HTTPException, match="image_storage_unavailable"):
        service.image(TOKEN, image)


@pytest.mark.parametrize(
    "headers,content",
    [
        ({"Content-Type": "text/html"}, b"html"),
        ({"Content-Type": "image/jpeg", "Content-Encoding": "gzip"}, b"compressed"),
        ({"Content-Type": "image/jpeg"}, b"x" * (2 * 1024 * 1024 + 1)),
    ],
    ids=["wrong-type", "encoded", "oversized"],
)
def test_public_image_rejects_invalid_response(store, headers, content):
    image = uuid4()
    store._request.return_value = {"object_path": f"{uuid4()}/{image}.jpg"}
    service = PostSharingService(
        store,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, content=content)
        ),
    )
    with pytest.raises(HTTPException):
        service.image(TOKEN, image)
