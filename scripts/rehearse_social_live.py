"""Explicit live acceptance: two scoped Auth users, loopback API, development DB only.

prepare writes private SQL/credentials in ignored .local; run uses real user sessions.
No service-role key, mail, paid model, deployment, or third-party messages.
"""

import argparse
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/social-acceptance"
MANIFEST = STATE / "fixture.json"
BASE = "http://127.0.0.1:8000/api/v1"


def save(value):
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture():
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert value["scope"].startswith("social-live-") and len(value["accounts"]) == 2
    for i, account in enumerate(value["accounts"]):
        UUID(account["id"])
        assert account["email"] == f"{value['scope']}-{i}@example.test"
    return value


def prepare():
    if MANIFEST.exists():
        raise SystemExit("Existing exact scope must be reviewed before preparing another fixture")
    STATE.mkdir(parents=True, exist_ok=True)
    value = {"scope": "social-live-" + uuid4().hex, "accounts": []}
    for i, name in enumerate(("小安（測試）", "小晴（測試）")):
        value["accounts"].append(
            {
                "id": str(uuid4()),
                "email": f"{value['scope']}-{i}@example.test",
                "password": secrets.token_urlsafe(32),
                "name": name,
            }
        )
    save(value)
    sql = ["begin;"]
    for i, a in enumerate(value["accounts"]):
        sql.append(f"""
insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
 confirmation_token,recovery_token,email_change_token_new,email_change,
 raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
values('00000000-0000-0000-0000-000000000000','{a["id"]}','authenticated','authenticated',
 '{a["email"]}',extensions.crypt('{a["password"]}',extensions.gen_salt('bf')),now(),'','','','',
 '{{"provider":"email","providers":["email"],"social_test_scope":"{value["scope"]}"}}',
 '{{"display_name":"{a["name"]}"}}',now(),now());
insert into auth.identities(user_id,provider_id,provider,identity_data,created_at,updated_at)
values('{a["id"]}','{a["id"]}','email','{{"sub":"{a["id"]}","email":"{a["email"]}","email_verified":true}}',now(),now());
insert into public.core_profiles(id,display_name,badge,bio) values('{a["id"]}','{a["name"]}',
 '{"🍜" if i == 0 else "🧋"}',
 '校園功能驗證帳號，貼文與訊息為合成測試資料。') on conflict do nothing;
""")
    ids = ",".join(f"'{a['id']}'::uuid" for a in value["accounts"])
    sql.append(f"""
insert into public.core_user_blocks(owner_id,blocked_id)
select a.id,b.id from public.core_profiles a cross join public.core_profiles b
where a.id=any(array[{ids}]) and not b.id=any(array[{ids}]) on conflict do nothing;
commit; select 'two isolated Auth fixtures prepared' as result;
""")
    (STATE / "prepare.sql").write_text("\n".join(sql), encoding="utf-8")
    print("Prepared two scoped test accounts; credentials remain private in ignored .local.")


def run():
    value = fixture()
    report = {"scope": value["scope"], "checks": [], "started_at": datetime.now(UTC).isoformat()}

    def passed(label):
        report["checks"].append(label)
        (STATE / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("PASS " + label, flush=True)

    with httpx.Client(base_url=BASE, trust_env=False, follow_redirects=False, timeout=30) as client:
        headers = []
        for a in value["accounts"]:
            r = client.post("/auth/login", json={k: a[k] for k in ("email", "password")})
            assert r.status_code == 200, f"Synthetic login returned {r.status_code}"
            session = r.json()
            assert session["user"]["id"] == a["id"] and session["user"]["auth_mode"] == "supabase"
            headers.append({"Authorization": "Bearer " + session["access_token"]})
        passed("two real independent Supabase Auth sessions")

        def call(method, path, actor=0, body=None, expected=200, key=None):
            h = {**headers[actor]}
            if key:
                h["Idempotency-Key"] = key
            response = client.request(method, path, headers=h, json=body)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}, expected {expected}"
            )
            if expected < 400:
                assert response.headers.get("cache-control") == "no-store"
            return response.json() if response.content else None

        assert client.get("/inbox").status_code == 401
        assert client.get("/account/experience").status_code == 401
        passed("anonymous inbox and reputation requests denied")
        location = {
            "name": "政大正門",
            "latitude": 24.9876,
            "longitude": 121.576,
            "address": "入口右側集合，測試用地點",
            "source": "openstreetmap",
        }
        deadline = datetime.now(UTC) + timedelta(seconds=105)
        payload = {
            "content": "【雙帳號驗證】正門便當一起訂，只有測試帳號參加。",
            "tags": ["隔離驗證"],
            "capacity": 2,
            "price_per_person": "90",
            "deadline_at": deadline.isoformat(),
            "location": location,
        }
        post = call("POST", "/posts", body=payload, expected=201, key=str(uuid4()))
        second = call(
            "POST",
            "/posts",
            body={**payload, "content": "【雙帳號驗證】另筆飲料，驗證取消不計分。"},
            expected=201,
            key=str(uuid4()),
        )
        value["all_test_posts"] = value.get("all_test_posts", value.get("test_posts", [])) + [
            post["id"],
            second["id"],
        ]
        value["test_posts"] = [post["id"], second["id"]]
        save(value)
        assert call("GET", "/account/experience")["score"] == 0
        call("GET", f"/conversations/{post['id']}/messages", 1, expected=403)
        dm = call("POST", "/direct-messages", 1, {"post_id": post["id"]})
        value["thread_id"] = dm["id"]
        save(value)
        assert call("POST", "/direct-messages", 1, {"post_id": post["id"]})["id"] == dm["id"]
        path = f"/direct-messages/{dm['id']}"
        key = str(uuid4())
        message = call(
            "POST", path + "/messages", 1, {"content": "請問可以在正門拿便當嗎？"}, 201, key
        )
        assert (
            call("POST", path + "/messages", 1, {"content": message["content"]}, 201, key)["id"]
            == message["id"]
        )
        call("POST", path + "/messages", 1, {"content": "這是不同內容"}, 409, key)
        assert len(call("GET", path)["messages"]) == 1
        assert call("GET", "/inbox?kind=direct")["total_unread"] == 1
        call("POST", path + "/read", body={"message_id": message["id"]})
        assert call("GET", "/inbox?kind=direct")["total_unread"] == 0
        reply = call(
            "POST",
            path + "/messages",
            body={"content": "可以，先確認品項再一起下單。"},
            expected=201,
            key=str(uuid4()),
        )
        assert call("GET", path, 1)["messages"][0]["id"] == reply["id"]
        assert call("GET", "/inbox?kind=direct", 1)["total_unread"] == 1
        call("POST", path + "/read", 1, {"message_id": reply["id"]})
        passed("non-member private inquiry, two-way replies, unread/read and idempotent retries")

        page = call("GET", path + "?limit=1")
        assert isinstance(page["next_cursor"], str)
        older = client.get(
            path, headers=headers[0], params={"limit": 1, "cursor": page["next_cursor"]}
        )
        assert (
            older.status_code == 200
            and older.json()["messages"][0]["id"] != page["messages"][0]["id"]
        )
        pref = f"/inbox/direct/{dm['id']}"
        call("PATCH", pref, 1, {"pinned": True, "inbox": "primary", "muted": True})
        own = call("GET", "/inbox?kind=direct", 1)["items"][0]
        other = call("GET", "/inbox?kind=direct")["items"][0]
        assert own["pinned"] and own["muted"] and own["inbox"] == "primary"
        assert not other["pinned"] and not other["muted"] and other["inbox"] == "general"
        call("PATCH", pref, 1, {"deleted": True})
        assert not any(t["id"] == dm["id"] for t in call("GET", "/inbox?kind=direct", 1)["items"])
        assert any(
            t["id"] == dm["id"] for t in call("GET", "/inbox?kind=direct&deleted=true", 1)["items"]
        )
        assert any(t["id"] == dm["id"] for t in call("GET", "/inbox?kind=direct")["items"])
        call(
            "PATCH",
            pref,
            1,
            {"deleted": False, "pinned": False, "muted": False, "inbox": "general"},
        )
        assert len(call("GET", path, 1)["messages"]) == 2
        passed(
            "personal pin/category/mute, delete and restore preserve the other account and messages"
        )

        join = call(
            "POST",
            f"/posts/{post['id']}/join-requests",
            1,
            {"expected_version": post["version"]},
            201,
        )
        accepted = call("PATCH", f"/join-requests/{join['id']}", body={"status": "accepted"})
        assert accepted["requester_badge"] == "🧋" and accepted["requester_level"] == 1
        call(
            "POST",
            f"/conversations/{post['id']}/messages",
            1,
            {"content": "我已加入，取餐前再確認。"},
            201,
            str(uuid4()),
        )
        assert len(call("GET", f"/orders/{post['id']}/summary", 1)["members"]) == 2
        assert all(t["kind"] == "group" for t in call("GET", "/inbox?kind=group", 1)["items"])
        assert all(t["kind"] == "direct" for t in call("GET", "/inbox?kind=direct", 1)["items"])
        assert {t["kind"] for t in call("GET", "/inbox", 1)["items"]} == {"direct", "group"}
        call("PATCH", f"/inbox/group/{post['id']}", 1, {"pinned": True, "inbox": "primary"})
        assert call("GET", "/inbox", 1)["items"][0]["kind"] == "group"
        assert not call("GET", "/inbox?kind=group")["items"][0]["pinned"]
        first = call("GET", "/inbox?limit=1", 1)
        assert first["next_cursor"]
        next_page = client.get(
            "/inbox", headers=headers[1], params={"limit": 1, "cursor": first["next_cursor"]}
        )
        assert (
            next_page.status_code == 200
            and next_page.json()["items"][0]["id"] != first["items"][0]["id"]
        )
        assert (
            client.get(
                "/inbox", headers=headers[0], params={"limit": 1, "cursor": first["next_cursor"]}
            ).status_code
            == 422
        )
        passed("group membership, filters, pinned ordering and cursor ownership")
        call("POST", f"/posts/{second['id']}/cancel")
        call("POST", f"/orders/{second['id']}/handoff", expected=409)
        call("POST", f"/orders/{post['id']}/handoff", expected=409)
        assert call("GET", "/account/experience")["score"] == 0
        passed("no score for cancellation, creation, membership or premature handoff")

        wait = max(0, (deadline - datetime.now(UTC)).total_seconds() + 2)
        print(
            f"Waiting {round(wait)} seconds for the real server deadline; no clock bypass.",
            flush=True,
        )
        while wait > 0:
            time.sleep(min(10, wait))
            wait = max(0, (deadline - datetime.now(UTC)).total_seconds() + 2)
        assert not call("POST", f"/orders/{post['id']}/handoff")["completed"]
        assert call("GET", "/account/experience")["score"] == 0
        assert call("POST", f"/orders/{post['id']}/handoff", 1)["completed"]
        for actor in (0, 1):
            earned = call("GET", "/account/experience", actor)
            assert (
                earned["score"] == 10 and earned["level"] == 1 and earned["completed_orders"] == 1
            )
            assert post["id"] in earned["completed_post_ids"]
            call("POST", f"/orders/{post['id']}/handoff", actor)
            assert call("GET", "/account/experience", actor)["score"] == 10
        passed("all members confirm after deadline: both earn 10 once, no duplicate points")
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["result"] = "passed"
        (STATE / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else run()
