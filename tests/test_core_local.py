import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core_local import dependencies
from app.core_local.store import CoreStore, password_hash
from app.main import app

PASSWORD = "Synthetic-password-123!"


@pytest.fixture
def core_api(tmp_path, monkeypatch):
    store = CoreStore(tmp_path / "core.sqlite3")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    monkeypatch.setattr(dependencies, "local_store", lambda: store)
    dependencies._attempts.clear()
    with TestClient(app) as client:
        yield client, store
    get_settings.cache_clear()
    dependencies._attempts.clear()


def register(client, name):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{name}@example.test",
            "password": PASSWORD,
            "display_name": name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), {"Authorization": f"Bearer {response.json()['access_token']}"}


def post_body(**changes):
    return {
        "content": "本機測試：今天一起訂晚餐並自行協調取餐。",
        "tags": ["晚餐"],
        "deadline_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "capacity": 2,
        "location": {"name": "政大正門", "latitude": 24.987, "longitude": 121.576},
        "price_per_person": "85.50",
        **changes,
    }


def create_post(client, headers, body=None, key=None):
    response = client.post(
        "/api/v1/posts",
        headers={
            **headers,
            "Idempotency-Key": key or str(uuid4()),
        },
        json=body or post_body(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_email_login_logout_and_hashed_persistence(core_api):
    client, store = core_api
    session, headers = register(client, "host")
    assert session["user"]["auth_mode"] == "local_development"
    assert session["user"]["email_verified"] is False
    assert "password" not in str(session["user"])
    assert client.get("/api/v1/auth/me", headers=headers).json()["display_name"] == "host"
    assert CoreStore(store.path).authenticate(session["access_token"]).id == UUID(
        session["user"]["id"]
    )
    with store.transaction() as db:
        row = dict(db.execute("SELECT * FROM core_users").fetchone())
        assert PASSWORD not in str(row)
        assert session["access_token"] not in str(
            db.execute("SELECT * FROM core_sessions").fetchall()
        )
    response = client.post(
        "/api/v1/auth/login", json={"email": " HOST@EXAMPLE.TEST ", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "host@example.test", "password": "wrong-password"}
        ).status_code
        == 401
    )
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_host_approval_withdrawal_and_released_seat(core_api):
    client, store = core_api
    host, h = register(client, "host")
    _, a = register(client, "alice")
    _, b = register(client, "bob")
    post = create_post(client, h)
    assert post["joined"] == 1 and post["my_status"] == "host"
    post_id = post["id"]
    url = f"/api/v1/posts/{post_id}"
    assert client.get(url + "/join-requests", headers=a).status_code == 403
    first = client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).json()
    assert first["status"] == "pending"
    assert (
        client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).json()["id"]
        == first["id"]
    )
    second = client.post(url + "/join-requests", headers=b, json={"expected_version": 1}).json()
    assert client.get(url, headers=a).json()["joined"] == 1
    decision = f"/api/v1/join-requests/{first['id']}"
    assert client.patch(decision, headers=b, json={"status": "accepted"}).status_code == 403
    assert client.patch(decision, headers=h, json={"status": "accepted"}).status_code == 200
    assert client.patch(decision, headers=h, json={"status": "accepted"}).status_code == 200
    assert client.get(url, headers=b).json()["status"] == "full"
    second_url = f"/api/v1/join-requests/{second['id']}"
    assert client.patch(second_url, headers=h, json={"status": "accepted"}).status_code == 409
    assert client.delete(url + "/membership", headers=a).json()["joined"] == 1
    assert client.delete(url + "/membership", headers=a).json()["joined"] == 1
    assert client.patch(second_url, headers=h, json={"status": "accepted"}).status_code == 200
    assert client.get(url, headers=b).json()["my_status"] == "accepted"
    assert client.get("/api/v1/posts?scope=participating", headers=a).json()["items"] == []
    reopened = CoreStore(store.path).post(UUID(host["user"]["id"]), UUID(post_id))
    assert reopened.joined == 2


def test_deadline_blocks_join_accept_and_withdraw(core_api, monkeypatch):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    _, b = register(client, "bob")
    post = create_post(client, h)
    url = f"/api/v1/posts/{post['id']}"
    request = client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).json()
    future = datetime.fromisoformat(post["deadline_at"]) + timedelta(seconds=1)
    monkeypatch.setattr("app.core_local.store.utc_now", lambda: future)
    assert client.get(url, headers=a).json()["status"] == "expired"
    assert (
        client.post(url + "/join-requests", headers=b, json={"expected_version": 1}).status_code
        == 409
    )
    assert client.delete(url + "/membership", headers=a).status_code == 409
    assert (
        client.patch(
            f"/api/v1/join-requests/{request['id']}", headers=h, json={"status": "accepted"}
        ).status_code
        == 409
    )


def test_history_preserves_rejections_and_withdrawals_without_showing_other_users(core_api):
    client, _ = core_api
    _, host = register(client, "history-host")
    _, member = register(client, "history-member")
    _, stranger = register(client, "history-stranger")
    post = create_post(client, host)
    path = f"/api/v1/posts/{post['id']}"
    request = client.post(
        path + "/join-requests", headers=member, json={"expected_version": 1}
    ).json()
    decision = client.patch(
        f"/api/v1/join-requests/{request['id']}", headers=host, json={"status": "rejected"}
    )
    assert decision.status_code == 200
    history = client.get("/api/v1/posts?scope=history", headers=member).json()["items"]
    assert len(history) == 1 and history[0]["my_status"] == "rejected"
    assert client.get("/api/v1/posts?scope=history", headers=stranger).json()["items"] == []
    assert client.get("/api/v1/posts?scope=history", headers=host).json()["items"] == []
    assert (
        client.post(
            path + "/join-requests", headers=member, json={"expected_version": 1}
        ).status_code
        == 201
    )
    assert client.get("/api/v1/posts?scope=history", headers=member).json()["items"] == []
    assert client.delete(path + "/membership", headers=member).status_code == 200
    assert (
        client.get("/api/v1/posts?scope=history", headers=member).json()["items"][0]["my_status"]
        == "cancelled"
    )
    assert client.post(path + "/cancel", headers=host).status_code == 200
    assert (
        client.get("/api/v1/posts?scope=history", headers=host).json()["items"][0]["status"]
        == "cancelled"
    )


def test_public_profile_has_no_private_fields_and_filters_posts_by_id(core_api):
    client, _ = core_api
    first, first_headers = register(client, "public-first")
    _, second_headers = register(client, "public-second")
    for headers in (first_headers, second_headers):
        response = client.patch(
            "/api/v1/auth/me",
            headers=headers,
            json={
                "display_name": "相同名稱",
                "bio": "公開介紹",
                "badge": "🍱",
                "expected_version": 1,
            },
        )
        assert response.status_code == 200
    post = create_post(client, first_headers)
    create_post(client, second_headers)
    path = f"/api/v1/users/{first['user']['id']}"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=second_headers)
    assert response.status_code == 200
    assert set(response.json()) == {
        "id",
        "display_name",
        "bio",
        "badge",
        "created_at",
        "active_posts",
    }
    assert response.json()["display_name"] == "相同名稱" and response.json()["active_posts"] == 1
    assert [
        item["id"] for item in client.get(path + "/posts", headers=second_headers).json()["items"]
    ] == [post["id"]]
    assert client.get(f"/api/v1/users/{uuid4()}", headers=second_headers).status_code == 404
    assert client.get(f"/api/v1/users/{uuid4()}/posts", headers=second_headers).status_code == 404


def test_duplicate_creation_and_payload_conflict(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    key, body = str(uuid4()), post_body()
    first = create_post(client, h, body, key)
    assert create_post(client, h, body, key)["id"] == first["id"]
    response = client.post(
        "/api/v1/posts", headers={**h, "Idempotency-Key": key}, json={**body, "capacity": 3}
    )
    assert response.status_code == 409
    assert len(client.get("/api/v1/posts", headers=h).json()["items"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"capacity": 1},
        {"capacity": 21},
        {"capacity": True},
        {"capacity": 2.5},
        {"price_per_person": "0.001"},
        {"price_per_person": -1},
        {"deadline_at": "2026-10-16T10:00:00"},
        {"tags": [""]},
        {"author_id": str(uuid4())},
        {"content": "short"},
    ],
)
def test_invalid_or_spoofed_post_rejected(core_api, change):
    client, _ = core_api
    _, h = register(client, "host")
    response = client.post(
        "/api/v1/posts", headers={**h, "Idempotency-Key": str(uuid4())}, json=post_body(**change)
    )
    assert response.status_code == 422


def test_parallel_host_decisions_cannot_overfill(core_api):
    client, store = core_api
    host, h = register(client, "host")
    alice, a = register(client, "alice")
    bob, b = register(client, "bob")
    post = create_post(client, h)
    url = f"/api/v1/posts/{post['id']}/join-requests"
    ids = [
        client.post(url, headers=header, json={"expected_version": 1}).json()["id"]
        for header in (a, b)
    ]
    actor = UUID(host["user"]["id"])

    def accept(join_id):
        try:
            store.decide(actor, UUID(join_id), "accepted")
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, ids)) == [200, 409]
    assert store.post(actor, UUID(post["id"])).joined == 2


def test_local_boundary_auth_and_cancel(core_api, monkeypatch):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    assert client.get("/api/v1/posts").status_code == 401
    assert (
        client.get("/api/v1/posts", headers={**h, "Origin": "https://evil.example"}).status_code
        == 403
    )
    assert (
        client.get("/api/v1/posts", headers={**h, "Origin": "http://localhost:8081"}).status_code
        == 200
    )
    assert (
        client.get("/api/v1/posts", headers={**h, "X-Forwarded-For": "127.0.0.1"}).status_code
        == 403
    )
    post = create_post(client, h)
    url = f"/api/v1/posts/{post['id']}"
    assert client.post(url + "/cancel", headers=a).status_code == 403
    assert client.delete(url + "/membership", headers=h).status_code == 409
    assert client.post(url + "/cancel", headers=h).json()["status"] == "cancelled"
    assert (
        client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).status_code
        == 409
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert client.get("/api/v1/posts", headers=h).status_code == 503


def test_auth_rate_limit_and_invalid_cursor(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    assert client.get("/api/v1/posts?cursor=W10=", headers=h).status_code == 422
    for _ in range(19):
        client.post("/api/v1/auth/login", json={"email": "a@b.test", "password": "x"})
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "a@b.test", "password": PASSWORD}
        ).status_code
        == 429
    )


def test_session_expiry_and_duplicate_email_are_enforced(core_api, monkeypatch):
    client, _ = core_api
    _, h = register(client, "host")
    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "email": "HOST@EXAMPLE.TEST",
            "password": PASSWORD,
            "display_name": "another",
        },
    )
    assert duplicate.status_code == 409
    future = datetime.now(UTC) + timedelta(hours=25)
    monkeypatch.setattr("app.core_local.store.utc_now", lambda: future)
    response = client.get("/api/v1/auth/me", headers=h)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_never_echoes_passwords_or_request_values(core_api):
    client, _ = core_api
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "secret",
            "display_name": "test",
        },
    )
    assert response.status_code == 422
    assert "secret" not in response.text and "not-an-email" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_reject_reapply_and_cancel_preserve_history(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    post = create_post(client, h)
    url = f"/api/v1/posts/{post['id']}"
    first = client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).json()
    decision = f"/api/v1/join-requests/{first['id']}"
    assert client.patch(decision, headers=h, json={"status": "rejected"}).status_code == 200
    assert client.patch(decision, headers=h, json={"status": "accepted"}).status_code == 409
    again = client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).json()
    assert again["id"] == first["id"] and again["status"] == "pending"
    assert len(client.get(url + "/join-requests", headers=h).json()) == 1
    assert client.post(url + "/cancel", headers=h).status_code == 200
    assert client.patch(decision, headers=h, json={"status": "accepted"}).status_code == 409
    assert client.get(url, headers=a).json()["status"] == "cancelled"


def test_pagination_ownership_and_private_fields(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    for index in range(3):
        create_post(client, h, post_body(content=f"本機測試拼單第 {index} 筆，請自行協調取餐。"))
    response = client.get("/api/v1/posts?scope=hosted&limit=2", headers=h)
    assert response.headers["cache-control"] == "no-store"
    first = response.json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get(
        "/api/v1/posts",
        headers=h,
        params={"scope": "hosted", "limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert not {post["id"] for post in first["items"]} & {post["id"] for post in second["items"]}
    assert client.get("/api/v1/posts?scope=hosted", headers=a).json()["items"] == []
    assert "host@example.test" not in str(first)
    assert "password" not in str(first) and "salt" not in str(first)


def test_concurrent_duplicate_publication_creates_one_record(core_api):
    client, store = core_api
    host, h = register(client, "host")
    from app.core_local.schemas import PostInput

    value = PostInput.model_validate(post_body())
    actor, key = UUID(host["user"]["id"]), str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: store.create_post(actor, value, key).id, range(2)))
    assert ids[0] == ids[1]
    assert len(client.get("/api/v1/posts", headers=h).json()["items"]) == 1


def test_adapter_is_disabled_and_paths_cannot_escape_workspace(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/v1/posts").status_code == 503
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_DB_PATH", "../must-not-be-created.sqlite3")
    get_settings.cache_clear()
    dependencies.local_store.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/posts").status_code == 503
    finally:
        get_settings.cache_clear()
        dependencies.local_store.cache_clear()


def test_search_filters_real_posts_and_escapes_wildcards(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    dinner = create_post(
        client, h, post_body(content="今天晚餐一起吃，優惠折扣為 10%。", tags=["晚餐"])
    )
    coffee = create_post(
        client, a, post_body(content="下午一起訂咖啡，請到校門口取餐。", tags=["咖啡"])
    )

    def search(q, **parameters):
        response = client.get("/api/v1/posts", headers=h, params={"q": q, **parameters})
        assert response.status_code == 200, response.text
        return {post["id"] for post in response.json()["items"]}

    assert search("晚餐") == {dinner["id"]}
    assert search("咖啡") == {coffee["id"]}
    assert search("alice") == {coffee["id"]}
    assert search("%") == {dinner["id"]}
    assert search("_") == set()
    assert search("' OR 1=1 --") == set()
    assert search("咖啡", scope="hosted") == set()
    assert client.post(f"/api/v1/posts/{dinner['id']}/cancel", headers=h).status_code == 200
    assert search("晚餐", active_only="true") == set()
    assert search("晚餐", active_only="false") == {dinner["id"]}


def test_failed_publication_rolls_back_without_partial_records(core_api, monkeypatch):
    client, store = core_api
    host, h = register(client, "host")
    from app.core_local.schemas import PostInput

    value = PostInput.model_validate(post_body())

    def fail_after_insert(*_args):
        raise RuntimeError("synthetic failure after insertion")

    with monkeypatch.context() as patcher:
        patcher.setattr(store, "_view", fail_after_insert)
        with pytest.raises(RuntimeError):
            store.create_post(UUID(host["user"]["id"]), value, str(uuid4()))
    assert client.get("/api/v1/posts", headers=h).json()["items"] == []


def test_profile_edits_persist_and_reject_stale_or_spoofed_updates(core_api):
    client, store = core_api
    session, h = register(client, "host")
    _, a = register(client, "alice")
    post = create_post(client, h)
    value = {
        "display_name": "更新後的主揪",
        "bio": "一起訂餐",
        "badge": "🍕",
        "expected_version": 1,
    }
    response = client.patch("/api/v1/auth/me", headers=h, json=value)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2 and response.json()["email"] == "host@example.test"
    assert client.get("/api/v1/auth/me", headers=a).json()["display_name"] == "alice"
    assert client.patch("/api/v1/auth/me", headers=h, json=value).status_code == 409
    assert (
        client.patch(
            "/api/v1/auth/me", headers=h, json={**value, "expected_version": 2, "id": str(uuid4())}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/auth/me",
            headers=h,
            json={**value, "expected_version": 2, "email": "other@example.test"},
        ).status_code
        == 422
    )
    current_post = client.get(f"/api/v1/posts/{post['id']}", headers=a).json()
    assert current_post["author_name"] == "更新後的主揪" and current_post["author_badge"] == "🍕"
    reopened = CoreStore(store.path).authenticate(session["access_token"])
    assert reopened.bio == "一起訂餐" and reopened.version == 2


def test_local_profile_schema_upgrade_keeps_existing_accounts(tmp_path):
    path = tmp_path / "old-core.sqlite3"
    user_id = str(uuid4())
    salt = "ab" * 16
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE core_users (
            id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO core_users VALUES (?,?,?,?,?,?)",
            (
                user_id,
                "existing@example.test",
                "existing",
                salt,
                password_hash(PASSWORD, salt),
                datetime.now(UTC).isoformat(),
            ),
        )
    from app.core_local.schemas import LoginInput

    upgraded = CoreStore(path)
    session = upgraded.login(LoginInput(email="existing@example.test", password=PASSWORD))
    assert str(session.user.id) == user_id
    assert session.user.version == 1 and session.user.bio == "" and session.user.badge == "🍱"
    assert CoreStore(path).authenticate(session.access_token).id == session.user.id


def test_password_whitespace_is_significant_and_never_trimmed(core_api):
    client, _ = core_api
    password = "  synthetic-password  "
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": " spaces@example.test ",
            "display_name": "  名稱  ",
            "password": password,
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["display_name"] == "名稱"
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "spaces@example.test", "password": password}
        ).status_code
        == 200
    )

    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "spaces@example.test", "password": password.strip()},
        ).status_code
        == 401
    )


def test_post_edit_requires_host_fresh_version_and_no_active_requests(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    original = post_body()
    key = str(uuid4())
    post = create_post(client, h, original, key)
    url = f"/api/v1/posts/{post['id']}"
    assert post["can_edit"] is True and post["version"] == 1
    changed = {
        **original,
        "content": "更新後的晚餐拼單，請重新確認內容。",
        "capacity": 3,
        "expected_version": 1,
    }
    assert client.patch(url, headers=a, json=changed).status_code == 403
    updated = client.patch(url, headers=h, json=changed)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert client.patch(url, headers=h, json=changed).status_code == 409
    assert (
        client.post(url + "/join-requests", headers=a, json={"expected_version": 1}).status_code
        == 409
    )
    request = client.post(url + "/join-requests", headers=a, json={"expected_version": 2})
    assert request.status_code == 201
    assert client.get(url, headers=h).json()["can_edit"] is False
    changed["expected_version"] = 2
    assert client.patch(url, headers=h, json=changed).status_code == 409
    decision_url = f"/api/v1/join-requests/{request.json()['id']}"
    assert client.patch(decision_url, headers=h, json={"status": "accepted"}).status_code == 200
    assert client.patch(url, headers=h, json=changed).status_code == 409
    assert client.delete(url + "/membership", headers=a).status_code == 200
    assert client.patch(url, headers=h, json=changed).json()["version"] == 3
    # Creation retries still refer to the original resource, even after its owner edits it.
    assert create_post(client, h, original, key)["id"] == post["id"]


def test_join_requires_explicit_current_conditions_and_rejects_actor_spoofing(core_api):
    client, _ = core_api
    _, h = register(client, "host")
    _, a = register(client, "alice")
    post = create_post(client, h)
    url = f"/api/v1/posts/{post['id']}/join-requests"
    assert client.post(url, headers=a).status_code == 422
    assert client.post(url, headers=a, json={"expected_version": True}).status_code == 422
    assert (
        client.post(
            url, headers=a, json={"expected_version": 1, "requester_id": str(uuid4())}
        ).status_code
        == 422
    )
    assert client.get(f"/api/v1/posts/{post['id']}/join-requests", headers=h).json() == []


def test_racing_edit_and_join_cannot_accept_unseen_conditions(core_api):
    client, store = core_api
    host, h = register(client, "host")
    member, _ = register(client, "alice")
    original = post_body()
    post = create_post(client, h, original)
    from app.core_local.schemas import PostEditInput

    edit = PostEditInput.model_validate({**original, "capacity": 3, "expected_version": 1})

    def run(operation):
        try:
            if operation == "edit":
                store.edit_post(UUID(host["user"]["id"]), UUID(post["id"]), edit)
            else:
                store.request_join(UUID(member["user"]["id"]), UUID(post["id"]), 1)
            return operation, 200
        except HTTPException as error:
            return operation, error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(pool.map(run, ("edit", "join")))
    assert sorted(result.values()) == [200, 409]
    current = store.post(UUID(host["user"]["id"]), UUID(post["id"]))
    assert current.joined == 1
    assert current.version == (2 if result["edit"] == 200 else 1)
