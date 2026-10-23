import base64
import hashlib
import hmac
import io
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.api.routes import uploads
from app.api.routes.chat_photos import ChatMediaService
from app.core.config import get_settings
from app.core_cloud.images import normalize_image
from app.core_cloud.media import AssetView, MediaService
from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileView
from app.main import app


def png():
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "blue").save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("CORE_MEDIA_ENABLED", "true")
    monkeypatch.setenv("CORE_MEDIA_SIGNING_KEY", "ab" * 32)
    get_settings.cache_clear()
    owner = uuid4()
    store = Mock(spec=SupabaseCoreStore)
    store.key = "sb_publishable_synthetic"
    store.token = "synthetic-session"
    store.authenticate.return_value = ProfileView(
        id=owner,
        email="fixture@example.test",
        display_name="fixture",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    yield store, owner
    get_settings.cache_clear()


def test_normalized_storage_upload_is_verified_before_server_attestation(configured):
    store, owner = configured
    image = normalize_image(png())
    image_id = uuid4()
    storage_id = uuid4()
    path = f"{owner}/{image_id}.jpg"
    requests = []
    stored = None

    def handle(request):
        nonlocal stored
        requests.append(request)
        assert request.url.host == httpx.URL(PROJECT_URL).host
        assert request.headers["Authorization"] == "Bearer synthetic-session"
        assert ("ab" * 32).encode() not in request.content
        if request.method == "POST":
            assert request.headers["x-upsert"] == "false"
            stored = request.content
            return httpx.Response(200, json={"Key": path})
        return httpx.Response(200, content=stored, headers={"Content-Type": "image/jpeg"})

    asset = {"id": str(image_id), "object_path": path, "width": 12, "height": 8, "state": "pending"}
    store._request.side_effect = [
        asset,
        {**asset, "storage_id": str(storage_id)},
        {**asset, "state": "ready"},
    ]
    service = MediaService(store, owner, transport=httpx.MockTransport(handle))
    result = service.upload(image, "synthetic-image-retry-key")
    assert result.state == "ready" and set(result.model_dump()) == {
        "id",
        "width",
        "height",
        "state",
    }
    ready = store._request.call_args.kwargs["body"]
    assert ready["action"] == "ready"
    message = f"{image_id}:{owner}:{image.sha256}:{storage_id}".encode()
    assert (
        ready["args"]["proof"]
        == hmac.new(bytes.fromhex("ab" * 32), message, hashlib.sha256).hexdigest()
    )
    assert len(requests) == 2 and stored == image.content


def test_chat_photos_use_an_opaque_private_object_path_and_chat_rpc(configured):
    store, owner = configured
    image = normalize_image(png())
    image_id, object_id = uuid4(), uuid4()
    path = f"{image_id}.jpg"
    requests = []

    def handle(request):
        requests.append(request)
        assert str(owner) not in str(request.url)
        assert "/core-chat-images/" in request.url.path
        return (
            httpx.Response(200, json={"Key": path})
            if request.method == "POST"
            else httpx.Response(200, content=image.content)
        )

    asset = {"id": str(image_id), "object_path": path, "width": 12, "height": 8, "state": "pending"}
    store._request.side_effect = [
        asset,
        {**asset, "storage_id": str(object_id)},
        {**asset, "state": "ready"},
    ]
    result = ChatMediaService(store, owner, transport=httpx.MockTransport(handle)).upload(
        image, "photo-upload-retry-key"
    )
    assert result.state == "ready" and len(requests) == 2
    assert all(
        call.args[1] == "/rest/v1/rpc/core_chat_media" for call in store._request.call_args_list
    )


def test_wrong_existing_bytes_cannot_be_attested_even_after_duplicate_upload(configured):
    store, owner = configured
    image = normalize_image(png())
    image_id = uuid4()
    asset = {
        "id": str(image_id),
        "object_path": f"{owner}/{image_id}.jpg",
        "width": 12,
        "height": 8,
        "state": "pending",
    }
    store._request.side_effect = [asset, {**asset, "storage_id": str(uuid4())}]

    def handle(request):
        return (
            httpx.Response(400, json={"error": "Duplicate"})
            if request.method == "POST"
            else httpx.Response(200, content=b"changed-file")
        )

    with pytest.raises(HTTPException) as error:
        MediaService(store, owner, transport=httpx.MockTransport(handle)).upload(
            image, "synthetic-image-retry-key"
        )
    assert error.value.detail == "image_upload_conflict"
    assert all(call.kwargs["body"]["action"] != "ready" for call in store._request.call_args_list)


@pytest.mark.parametrize(
    "signed",
    [
        "https://evil.invalid/object/sign/private?token=x",
        "/object/sign/another-bucket/x?token=x",
        "/object/sign/core-post-images/other/x.jpg?token=x",
    ],
)
def test_signing_response_cannot_redirect_client_to_another_host_or_object(configured, signed):
    store, owner = configured
    image_id = uuid4()
    store._request.return_value = {
        "id": str(image_id),
        "object_path": f"{owner}/{image_id}.jpg",
        "width": 12,
        "height": 8,
        "state": "ready",
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"signedURL": signed}))
    with pytest.raises(HTTPException) as error:
        MediaService(store, owner, transport=transport).url(image_id)
    assert error.value.detail == "image_storage_unavailable"


def test_storage_redirects_are_never_followed(configured):
    store, owner = configured
    image = normalize_image(png())
    image_id = uuid4()
    requests = []
    store._request.return_value = {
        "id": str(image_id),
        "object_path": f"{owner}/{image_id}.jpg",
        "width": 12,
        "height": 8,
        "state": "pending",
    }

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/upload"})

    with pytest.raises(HTTPException):
        MediaService(store, owner, transport=httpx.MockTransport(handle)).upload(
            image, "synthetic-image-retry-key"
        )
    assert len(requests) == 1 and requests[0].url.host == httpx.URL(PROJECT_URL).host


@pytest.fixture
def media_client(configured):
    store, owner = configured
    service = Mock(spec=MediaService)
    service.upload.return_value = AssetView(id=uuid4(), width=12, height=8, state="ready")
    app.dependency_overrides[dependencies.get_store] = lambda: store
    app.dependency_overrides[uploads.media] = lambda: service
    try:
        with TestClient(app) as client:
            yield client, service
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        app.dependency_overrides.pop(uploads.media, None)


def test_api_normalizes_binary_input_and_never_echoes_base64_or_metadata(media_client):
    client, service = media_client
    body = base64.b64encode(png()).decode()
    result = client.post(
        "/api/v1/uploads/images",
        json={"content_base64": body},
        headers={"Idempotency-Key": "synthetic-image-upload"},
    )
    assert result.status_code == 201 and body not in result.text
    normalized = service.upload.call_args.args[0]
    assert normalized.content.startswith(b"\xff\xd8") and normalized.width == 12


@pytest.mark.parametrize(
    "body",
    [
        {"content_base64": "not-base64-private-input"},
        {"content_base64": base64.b64encode(b"<html>private-input</html>").decode()},
        {"content_base64": "a", "owner_id": "spoofed"},
    ],
)
def test_invalid_image_input_does_not_allocate_a_cloud_asset(media_client, body):
    client, service = media_client
    result = client.post(
        "/api/v1/uploads/images", json=body, headers={"Idempotency-Key": "synthetic-image-upload"}
    )
    assert result.status_code in (415, 422) and "private-input" not in result.text
    service.upload.assert_not_called()


def test_whole_body_limit_rejects_before_json_parsing(media_client):
    client, service = media_client
    response = client.post(
        "/api/v1/uploads/images",
        content=b"x" * (6 * 1024 * 1024 + 1),
        headers={"Content-Type": "application/json", "Idempotency-Key": "synthetic-image-upload"},
    )
    assert response.status_code == 413
    service.upload.assert_not_called()


def test_media_requires_authentication_and_is_opt_in(configured, monkeypatch):
    store, _ = configured
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/uploads/images").status_code == 401
            monkeypatch.setenv("CORE_MEDIA_ENABLED", "false")
            get_settings.cache_clear()
            response = client.get(
                "/api/v1/uploads/images", headers={"Authorization": "Bearer synthetic-session"}
            )
            assert (
                response.status_code == 503
                and response.json()["detail"] == "image_storage_not_configured"
            )
            store._request.assert_not_called()
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
