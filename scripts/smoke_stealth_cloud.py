"""Opt-in live acceptance against loopback API and scoped Supabase development project.

prepare writes disposable fixture SQL for reviewed MCP execution; run never provisions
accounts or bypasses Auth. Credentials stay in ignored .local and are never printed.
"""

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local" / "stealth-e2e"
FIXTURE = STATE / "accounts.json"


def prepare():
    if FIXTURE.exists():
        raise SystemExit("Existing fixture manifest: clean up its exact scope first.")
    scope = "stealth-e2e-" + uuid4().hex
    accounts = [
        {
            "id": str(uuid4()),
            "email": f"{scope}-{i}@example.test",
            "password": secrets.token_urlsafe(32),
        }
        for i in range(3)
    ]
    STATE.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps({"scope": scope, "accounts": accounts}), encoding="utf-8")
    statements = ["begin;"]
    for i, account in enumerate(accounts):
        uid, email, password = account["id"], account["email"], account["password"]
        statements.append(f"""
insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
 confirmation_token,recovery_token,email_change_token_new,email_change,
 raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
values('00000000-0000-0000-0000-000000000000','{uid}','authenticated','authenticated',
 '{email}',extensions.crypt('{password}',extensions.gen_salt('bf')),now(),'','','','',
 '{{"provider":"email","providers":["email"],"stealth_test_scope":"{scope}"}}',
 '{{"display_name":"隔離偷揪驗證{i + 1}"}}',now(),now());
insert into auth.identities(user_id,provider_id,provider,identity_data,created_at,updated_at)
values('{uid}','{uid}','email','{{"sub":"{uid}","email":"{email}","email_verified":true}}',now(),now());
""")
    statements.append("commit; select 'isolated fixtures provisioned' as result;")
    (STATE / "prepare.sql").write_text("\n".join(statements), encoding="utf-8")
    ids = ",".join(f"'{a['id']}'::uuid" for a in accounts)
    cleanup = f"""begin;
do $guard$ begin
 if (select count(*) from auth.users where id=any(array[{ids}]) and
 raw_app_meta_data->>'stealth_test_scope'='{scope}')<>3 then
 raise exception 'Fixture identity/scope mismatch; cleanup refused'; end if;
 if exists(select 1 from public.core_stealth_proposals where owners && array[{ids}]
 and not owners <@ array[{ids}]) then
 raise exception 'Mixed real/test proposal; cleanup refused'; end if;
 if exists(select 1 from public.core_join_requests j join public.core_posts p on p.id=j.post_id
 where p.author_id=any(array[{ids}]) and not j.requester_id=any(array[{ids}])) then
 raise exception 'External member on fixture post; cleanup refused'; end if;
 if exists(select 1 from public.core_chat_messages m join public.core_posts p on p.id=m.post_id
 where p.author_id=any(array[{ids}]) and not m.sender_id=any(array[{ids}])) then
 raise exception 'External chat sender on fixture post; cleanup refused'; end if;
end $guard$;
delete from public.core_stealth_proposals where owners <@ array[{ids}] and owners && array[{ids}];
delete from public.core_join_requests where requester_id=any(array[{ids}]) and post_id in
 (select id from public.core_posts where author_id=any(array[{ids}]));
delete from public.core_posts where author_id=any(array[{ids}]);
delete from public.core_stealth_signals where owner_id=any(array[{ids}]);
delete from public.core_stealth_agents where owner_id=any(array[{ids}]);
delete from auth.users where id=any(array[{ids}])
 and raw_app_meta_data->>'stealth_test_scope'='{scope}';
commit;
select count(*) as remaining_fixture_accounts from auth.users
 where raw_app_meta_data->>'stealth_test_scope'='{scope}';
"""
    (STATE / "cleanup.sql").write_text(cleanup, encoding="utf-8")
    print(f"Prepared three synthetic accounts, one isolated scope. Review SQL in {STATE}.")


def run():
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scope, accounts = manifest["scope"], manifest["accounts"]
    if not scope.startswith("stealth-e2e-") or len(accounts) != 3:
        raise SystemExit("Invalid isolated fixture manifest")
    for a in accounts:
        UUID(a["id"])
        if not a["email"].startswith(scope + "-") or not a["email"].endswith("@example.test"):
            raise SystemExit("Refusing non-fixture account")
    results = []

    def passed(label):
        results.append(label)
        print("PASS " + label, flush=True)

    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1", trust_env=False, timeout=400
    ) as client:

        def call(method, path, index=0, value=None, expected=200, key=False):
            options = {"headers": {**headers[index]}}
            if key:
                options["headers"]["Idempotency-Key"] = str(uuid4())
            if value is not None:
                options["json"] = value
            response = client.request(method, path, **options)
            if response.status_code != expected:
                # Deliberately omit response bodies, credentials and user inputs.
                raise AssertionError(
                    f"{method} {path}: {response.status_code}, expected {expected}"
                )
            return response.json() if response.content else None

        headers = []
        for account in accounts:
            response = client.post(
                "/auth/login", json={k: account[k] for k in ("email", "password")}
            )
            assert response.status_code == 200, f"fixture login status {response.status_code}"
            session = response.json()
            assert session["user"]["id"] == account["id"]
            headers.append({"Authorization": "Bearer " + session["access_token"]})
        passed("three real Supabase Auth sessions for disposable fixtures")
        start = datetime.now(UTC)
        base = "/stealth-cloud"
        for i in range(2):
            call(
                "PUT",
                base + "/agent",
                i,
                {
                    "name": "隔離語義驗證分身",
                    "enabled": True,
                    "daily_invite_limit": 10,
                    "timezone": "Asia/Taipei",
                    "quiet_start_hour": None,
                    "quiet_end_hour": None,
                    "exploration": "balanced",
                    "tone": "warm",
                    "avatar_key": "cat",
                    "expected_version": 0,
                    "include_demo": False,
                },
            )
            call(
                "POST",
                base + "/signals",
                i,
                {
                    "kind": "need" if i == 0 else "offer",
                    "category": "meal",
                    "acceptable_categories": [],
                    "title": "隔離熱湯需求" if i == 0 else "隔離熱湯分享資源",
                    "location_id": "east_gate",
                    "starts_at": (start + timedelta(hours=1)).isoformat(),
                    "ends_at": (start + timedelta(hours=2)).isoformat(),
                    "expires_at": (start + timedelta(minutes=50)).isoformat(),
                    "budget_max_twd": 150,
                    "known_cost_twd": None,
                    "tags": ["隔離語義驗證"],
                    "roles": ["order", "share"] if i == 0 else ["pickup", "share"],
                    "dietary_requirements": [],
                    "dietary_verified": [],
                },
                expected=201,
                key=True,
            )
        mine = call("GET", base + "/matches")["items"]
        if not mine:
            call("POST", base + "/matches/run", value={"max_proposals": 3})
            mine = call("GET", base + "/matches")["items"]
        assert len(mine) == 1 and mine[0]["member_count"] == 2 and not mine[0]["is_demo"]
        proposal = mine[0]["id"]
        assert call("GET", base + "/matches", 2)["items"] == []
        passed("private opted-in need/resource matching without demo or stranger access")
        semantic = call("POST", base + "/matches/semantic", value={"review_limit": 3})
        ranking = semantic["ranking"]
        assert ranking["status"] == "completed" and ranking["requires_unanimous_confirmation"]
        assert len(ranking["items"]) == 1 and ranking["items"][0]["review_status"] == "completed"
        assert ranking["items"][0]["review"]["verdict"] == "uncertain"
        assert semantic["items"][0]["confirmed_count"] == 0
        passed("real free local embeddings and grounded review preserve unknown price and consent")
        path = base + f"/matches/{proposal}"
        for i in range(2):
            call("POST", path + "/decision", i, {"decision": "accepted"})
        current = [call("GET", base + "/matches", i)["items"][0] for i in range(2)]
        terms = {
            "content": "隔離偷揪全流程驗證，虛構餐點沒有實際付款或訂購。",
            "cost_per_person_twd": 90,
            "deadline_at": (start + timedelta(minutes=20)).isoformat(),
            "order_member": current[0]["my_member_number"],
            "pickup_member": current[1]["my_member_number"],
            "cost_change_note": "",
        }
        first = call("PUT", path + "/terms", value={"expected_revision": 0, "value": terms})
        assert first["terms_revision"] == 1 and first["confirmed_count"] == 0
        call("POST", path + "/consent", 2, {"expected_revision": 1, "agree": True}, expected=404)
        one = call("POST", path + "/consent", value={"expected_revision": 1, "agree": True})
        assert one["confirmed_count"] == 1 and one["formed_post_id"] is None
        revised = call(
            "PUT",
            path + "/terms",
            value={"expected_revision": 1, "value": {**terms, "cost_per_person_twd": 95}},
        )
        assert revised["terms_revision"] == 2 and revised["confirmed_count"] == 0
        call("POST", path + "/consent", 1, {"expected_revision": 1, "agree": True}, expected=409)
        passed(
            "stranger denied, one consent cannot form; edits clear consent and reject old revision"
        )
        call("POST", path + "/consent", value={"expected_revision": 2, "agree": True})
        formed = call("POST", path + "/consent", 1, {"expected_revision": 2, "agree": True})
        post_id = formed["formed_post_id"]
        assert formed["status"] == "formed" and post_id and formed["confirmed_count"] == 2
        replay = call("POST", path + "/consent", 1, {"expected_revision": 2, "agree": True})
        assert replay["formed_post_id"] == post_id
        for i in range(2):
            post = call("GET", f"/posts/{post_id}", i)
            assert post["joined"] == post["capacity"] == 2
            assert float(post["price_per_person"]) == 95
        passed("unanimous current revision creates one full core post with replay protection")
        chat = f"/conversations/{post_id}/messages"
        message = call(
            "POST", chat, value={"content": "隔離驗證訊息，確認取餐分工。"}, expected=201, key=True
        )
        page = call("GET", chat, 1)
        assert any(m["id"] == message["id"] for m in page["messages"])
        call("GET", chat, 2, expected=403)
        passed("formed group chat permits both members and denies outsider")
        report = {
            "scope": scope,
            "passed": results,
            "proposal_id": proposal,
            "post_id": post_id,
            "ranking": ranking,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        (STATE / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for i in range(3):
            call("POST", "/auth/logout", i, expected=204)
        print("Live acceptance passed; run reviewed cleanup.sql to remove only this fixture scope.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "forget-credentials"])
    action = parser.parse_args().action
    if action == "prepare":
        prepare()
    elif action == "run":
        run()
    else:
        for name in ("accounts.json", "prepare.sql"):
            (STATE / name).unlink(missing_ok=True)
        print("Local disposable credentials removed; evidence report retained.")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
