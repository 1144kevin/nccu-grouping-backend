import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core_cloud.account_deletion import AccountDeletion, DeletionConfirm
from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore


def configured(items):
    actor = uuid4()
    store = Mock(spec=SupabaseCoreStore)
    store.key, store.token = "sb_publishable_synthetic", "synthetic-session"
    store._request.side_effect = [{"phase": "deleting"}, {"items": items(actor)}, {"deleted": True}]
    return store, actor


def test_deletion_removes_actual_owned_blobs_before_final_auth_cleanup():
    image = uuid4()
    store, actor = configured(lambda a: [{"path": f"{a}/{image}.jpg"}])
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == httpx.URL(PROJECT_URL).host
        assert request.method == "DELETE"
        assert request.headers["Authorization"] == "Bearer synthetic-session"
        assert request.url.path == "/storage/v1/object/core-post-images"
        return httpx.Response(200, json=[])

    result = AccountDeletion(store, actor, transport=httpx.MockTransport(handle)).delete(
        DeletionConfirm(confirmation="DELETE", challenge="x" * 43)
    )
    assert result.deleted is True and len(requests) == 1
    assert [call.kwargs["body"]["action"] for call in store._request.call_args_list] == [
        "begin",
        "assets",
        "finish",
    ]


@pytest.mark.parametrize("status", [403, 503, 302])
def test_storage_failure_or_redirect_does_not_finalize_the_account(status):
    store, actor = configured(lambda a: [{"path": f"{a}/{uuid4()}.jpg"}])
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, headers={"Location": "https://foreign.example/"})
    )
    with pytest.raises(HTTPException) as error:
        AccountDeletion(store, actor, transport=transport).delete(
            DeletionConfirm(confirmation="DELETE")
        )
    assert error.value.detail == "account_deletion_cleanup_failed"
    assert store._request.call_count == 2


@pytest.mark.parametrize(
    "path",
    [
        "other-owner/11111111-1111-4111-8111-111111111111.jpg",
        "{actor}/../foreign.jpg",
        "{actor}/x.jpg",
        "https://foreign.example/file.jpg",
    ],
)
def test_unexpected_storage_paths_cannot_be_deleted(path):
    store, actor = configured(lambda a: [{"path": path.replace("{actor}", str(a))}])
    with pytest.raises(HTTPException):
        AccountDeletion(store, actor).delete(DeletionConfirm(confirmation="DELETE"))
    assert store._request.call_count == 2


def test_account_without_images_does_not_require_a_storage_or_admin_key():
    store, actor = configured(lambda _: [])
    assert AccountDeletion(store, actor).delete(DeletionConfirm(confirmation="DELETE")).deleted
    assert store._request.call_count == 3


def test_cleanup_deletes_opaque_chat_photos_from_their_own_bucket():
    photo = uuid4()
    store, actor = configured(lambda _: [{"path": f"{photo}.jpg", "bucket": "core-chat-images"}])
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/storage/v1/object/core-chat-images"
        assert json.loads(request.content) == {"prefixes": [f"{photo}.jpg"]}
        return httpx.Response(200, json=[])

    assert (
        AccountDeletion(store, actor, transport=httpx.MockTransport(handle))
        .delete(DeletionConfirm(confirmation="DELETE"))
        .deleted
    )
    assert len(requests) == 1


@pytest.mark.parametrize("value", [True, 1, "delete", "", None])
def test_only_explicit_exact_deletion_confirmation_is_accepted(value):
    with pytest.raises(ValidationError):
        DeletionConfirm(confirmation=value)


def test_confirm_cannot_target_another_account_or_include_a_password():
    with pytest.raises(ValidationError):
        DeletionConfirm(confirmation="DELETE", owner_id=str(uuid4()))
    with pytest.raises(ValidationError):
        DeletionConfirm(confirmation="DELETE", password="not-needed")
