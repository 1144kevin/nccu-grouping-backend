"""Upload reviewed licensed photos through the real user-session image pipeline.

Requires the two isolated social acceptance accounts; provenance is marked by
reviewed SQL before their isolation blocks are removed. Never uses an admin key.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from scripts.refresh_showcase_content import location
from scripts.rehearse_social_live import BASE, fixture

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/showcase-photos"
OUTPUT = STATE / "published.json"
ENTRIES = [
    (
        "croissant",
        "明早的可頌一起買",
        "想一起買可頌當早餐，先在聊天室確認口味和數量，再一起下單。",
        65,
        4,
        0,
        15,
    ),
    (
        "tea",
        "下課來杯泰式奶茶",
        "想喝泰式奶茶的同學一起訂，甜度、冰塊和是否加珍珠先在聊天室確認。",
        65,
        5,
        1,
        3,
    ),
    (
        "pizza",
        "社課後一起分披薩",
        "揪同學一起分披薩，口味、分食份量和金額確認好再下單。",
        120,
        4,
        2,
        3,
    ),
    (
        "bento",
        "商學院活動便當免費領取",
        "活動結束後有未領取的便當，歡迎有需要的同學先申請。領取份數在聊天室確認，請自備餐具。",
        0,
        8,
        2,
        2,
    ),
]


def main():
    value = fixture()
    result = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    with httpx.Client(base_url=BASE, trust_env=False, follow_redirects=False, timeout=45) as client:
        tokens = []
        for a in value["accounts"]:
            response = client.post("/auth/login", json={k: a[k] for k in ("email", "password")})
            assert response.status_code == 200, f"fixture login {response.status_code}"
            session = response.json()
            assert session["user"]["id"] == a["id"]
            tokens.append({"Authorization": "Bearer " + session["access_token"]})

        def call(method, path, body=None, key=None, actor=0, status=200):
            headers = {**tokens[actor]}
            if key:
                headers["Idempotency-Key"] = key
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == status, f"{method} {path}: {response.status_code}"
            return response.json()

        for slug, title, body, price, capacity, where, hours in ENTRIES:
            if slug not in result:
                place = location(where)
                tags = (
                    ["剩食分享", "免費領取", "便當"]
                    if slug == "bento"
                    else ["拼單", {"croissant": "麵包", "tea": "飲料", "pizza": "披薩"}[slug]]
                )
                result[slug] = {
                    "value": {
                        "content": (
                            f"{title}\n{body}\n在{place['name']}取餐，"
                            "加入後再確認碰面細節。\n圖片為情境示意。"
                        ),
                        "tags": [*tags, "政大附近", place["name"]],
                        "capacity": capacity,
                        "price_per_person": str(price),
                        "location": place,
                        "deadline_at": (datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
                    }
                }
                OUTPUT.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            entry = result[slug]
            raw = (STATE / f"{slug}.jpg").read_bytes()
            image = call(
                "POST",
                "/uploads/images",
                {"content_base64": base64.b64encode(raw).decode()},
                value["scope"] + "-image-" + slug,
                status=201,
            )
            assert image["state"] == "ready"
            post = call(
                "POST",
                "/uploads/publish",
                {"image_id": image["id"], "value": entry["value"]},
                value["scope"] + "-photo-post-" + slug,
                status=201,
            )
            assert (
                post["author_id"] == value["accounts"][0]["id"] and post["image_id"] == image["id"]
            )
            signed = call("GET", f"/uploads/images/{image['id']}", actor=1)
            assert signed["url"].startswith(
                "https://axdwbfhrkbovgrbngrrh.supabase.co/storage/v1/object/sign/"
            )
            photo = client.get(signed["url"])
            assert photo.status_code == 200 and photo.headers["content-type"].startswith(
                "image/jpeg"
            )
            assert client.get(f"/uploads/images/{image['id']}").status_code == 401
            entry.update(
                post_id=post["id"],
                asset_id=image["id"],
                width=image["width"],
                height=image["height"],
                verified_other_account=True,
            )
            OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"PASS {slug}: user upload, publish, signed image and anonymous denial",
                flush=True,
            )


if __name__ == "__main__":
    main()
