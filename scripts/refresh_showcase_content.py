"""Prepare a reviewed, reversible refresh of previously identified development fixtures."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.core_local.schemas import PostInput, ProfileInput

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/showcase-photos"
BATCHES = {"showcase_20261017_v1", "showcase_20261019_recommendations_v2"}
PLACES = [
    ("政大正門", 24.98756683, 121.57604992, "正門入口右側，領取前在聊天室確認"),
    ("麥側", 24.987812, 121.5748528, "指南路側門附近，確切集合點由主揪確認"),
    ("商學院", 24.9866747, 121.576555, "一樓入口，請在聊天室確認碰面位置"),
    ("莊九", 24.9865623, 121.5783596, "宿舍外側入口，不進入住宿區"),
    ("藝文中心", 24.9815737, 121.5721284, "建築入口旁，取餐前再確認位置"),
    ("政大後門", 24.9797561, 121.568145, "後門入口附近，以主揪確認的位置為準"),
]
TITLES = {
    "便當": [
        "下課一起訂便當",
        "晚餐想吃便當，找同學一起訂",
        "趕報告也要好好吃飯",
        "今天不想一個人吃晚餐",
    ],
    "披薩": [
        "社課後一起分披薩",
        "揪幾個人一起吃披薩",
        "報告告一段落，披薩晚餐團",
        "下課來分披薩吧",
    ],
    "咖啡": ["讀書前一起買咖啡", "圖書館讀書的咖啡補給", "午后咖啡一起外帶", "趕進度需要一杯咖啡"],
    "麵包": ["明早的可頌一起買", "想揪同學一起訂麵包", "早餐麵包先揪起來", "一起買可頌當早餐"],
    "飲料": ["下課來杯奶茶", "讀書飲料團，甜度冰塊自己選", "一起訂珍珠奶茶", "社課前的飲料補給"],
    "蔬食": ["蔬食晚餐一起訂", "今天想吃蔬食便當", "找一起吃蔬食的夥伴", "晚餐來點蔬食"],
    "甜點": [
        "下午茶想找人一起分甜點",
        "小組討論的甜點時間",
        "一起買點心當讀書補給",
        "飯後甜點找夥伴",
    ],
    "宵夜": [
        "趕報告的宵夜團",
        "讀書讀餓了，一起找宵夜",
        "晚自習後一起吃點東西",
        "找同學一起訂宵夜",
    ],
}
BODIES = {
    "便當": "想找同學一起訂晚餐便當。先在聊天室確認主菜、飯量和金額，成團後由主揪統一下單。",
    "披薩": "一起分披薩比較剛好，口味與分食方式先在聊天室討論，成團後再下單。",
    "咖啡": "想喝咖啡的同學一起訂，品項、容量和是否加奶先在聊天室確認，方便一次拿取。",
    "麵包": "想一起買可頌和麵包當早餐，先確認各自口味與數量，再由主揪整理訂單。",
    "飲料": "奶茶、茶類都可以先提出，品項、甜度和冰塊請在聊天室寫清楚，確認後一起下單。",
    "蔬食": "一起訂蔬食餐點，有蛋奶或其他飲食需求請先提出，餐點成分確認後再決定品項。",
    "甜點": "想揪同學一起分甜點，品項、分食份量和費用在聊天室確認，不急著先付款。",
    "宵夜": "讀書或趕報告的同學可以一起訂宵夜，先討論品項與份量，再確認各自金額。",
}


def location(index):
    name, lat, lon, note = PLACES[index % len(PLACES)]
    return {
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "address": "取餐說明：" + note,
        "source": "openstreetmap",
    }


def main():
    before = json.loads((STATE / "before.json").read_text(encoding="utf-8"))
    assert len(before["posts"]) == 130 and len(before["profiles"]) == 16
    now = datetime.now(UTC)
    updates = []
    for index, post in enumerate(before["posts"]):
        UUID(post["id"])
        old = post["payload"]
        after = dict(old)
        deadline = post["deadline_at"]
        created = post["created_at"]
        if post["demo_batch"] in BATCHES:
            food = next((tag for tag in old["tags"] if tag in TITLES), list(TITLES)[index % 8])
            place = location(index)
            free = "免費分享" in old["tags"] or index % 11 == 0
            if free:
                meal = index % 2 == 0
                food = "便當" if meal else "麵包"
                title = "活動結束，便當開放同學領取" if meal else "多買的麵包，找同學一起分享"
                body = (
                    "活動結束後有未領取的便當，歡迎有需要的同學申請。請先在聊天室確認份數，自備餐具。"
                    if meal
                    else (
                        "今天多買了幾份麵包，想免費分享給有需要的同學。"
                        "口味和領取份數先在聊天室確認。"
                    )
                )
                price = "0.00"
                tags = ["剩食分享", "免費領取", food, "政大附近", place["name"]]
            else:
                title = TITLES[food][(index // 8) % 4]
                body = BODIES[food]
                price = {
                    "便當": "100.00",
                    "披薩": "120.00",
                    "咖啡": "60.00",
                    "麵包": "65.00",
                    "飲料": "55.00",
                    "蔬食": "95.00",
                    "甜點": "80.00",
                    "宵夜": "90.00",
                }[food]
                tags = ["拼單", "政大附近", food, place["name"]]
            after.update(
                content=f"{title}\n{body}\n在{place['name']}取餐，加入後再確認碰面細節。",
                tags=tags,
                price_per_person=price,
                location=place,
            )
            old_expired = index % 9 == 0 or "已經截止" in old["content"]
            dt = (
                now - timedelta(hours=3)
                if old_expired
                else now + timedelta(minutes=35 + index * 17 % 210)
            )
            if food == "麵包" and not free and not old_expired:
                dt = now + timedelta(hours=15)
            deadline = dt.isoformat()
            created = (now - timedelta(minutes=5 + index * 7 % 240)).isoformat()
            after["deadline_at"] = deadline
        elif post["id"] == "7c57e1ea-3437-4e9e-a5e1-6577b82105f4":
            after.update(
                content="商學院活動便當分享\n活動結束後有未領取的便當，開放同學免費領取。請先申請並在聊天室確認份數，自備餐具，到商學院入口找主揪。",
                tags=["剩食分享", "免費領取", "便當", "商學院"],
                price_per_person="0.00",
            )
        elif post["id"] == "48f6d5fe-eb50-4e09-b5e6-cea532a1d1ef":
            after.update(
                content=(
                    "晚餐想吃燒臘，找同學一起訂\n想吃叉燒、燒鴨或油雞便當，"
                    "配菜與飯量先在聊天室討論。每人預算 200 元以內，"
                    "實際品項和金額確認後再下單。"
                ),
                tags=["拼單", "晚餐", "燒臘", "政大附近"],
            )
        elif post["id"] == "8e513a42-4378-4be2-a20c-594d8aa970e6":
            after.update(
                content="政大正門晚餐便當團\n下課後一起訂晚餐，在政大正門取餐。加入後先確認餐點與金額，收到餐點再互相確認面交。",
                tags=["拼單", "便當", "晚餐", "政大正門"],
            )
        else:
            raise AssertionError("Refusing unidentified post")
        PostInput.model_validate(after)
        assert after["capacity"] == post["capacity"]
        updates.append(
            {
                "id": post["id"],
                "before": old,
                "version": post["version"],
                "after": after,
                "deadline": deadline,
                "created": created,
            }
        )
    names = [
        "小林",
        "怡安",
        "阿哲",
        "小米",
        "子晴",
        "宇翔",
        "阿軒",
        "小涵",
        "冠宇",
        "庭萱",
        "阿傑",
        "小妤",
        "柏翰",
        "品蓉",
        "小葳",
        "家豪",
    ]
    badges = ["🍜", "🥐", "🧋", "🍍", "🍕", "🍱", "🍜", "🍱"]
    profiles = [
        {
            "id": p["id"],
            "old_name": p["display_name"],
            "name": names[i],
            "badge": badges[i % len(badges)],
        }
        for i, p in enumerate(before["profiles"])
    ]
    for profile in profiles:
        ProfileInput.model_validate(
            {
                "display_name": profile["name"],
                "badge": profile["badge"],
                "bio": "校園情境示範人物，僅供體驗。",
                "expected_version": 1,
            }
        )
    data = json.dumps(updates, ensure_ascii=False).replace("'", "''")
    people = json.dumps(profiles, ensure_ascii=False).replace("'", "''")
    sql = f"""begin; set local lock_timeout='3s'; set local statement_timeout='15s';
do $guard$ begin
 if (select count(*) from jsonb_to_recordset('{data}'::jsonb) x(id uuid,before jsonb,version int)
 join public.core_posts p on p.id=x.id and p.payload=x.before and p.version=x.version)<>130 then
 raise exception 'Fixture changed since backup; refresh refused'; end if;
end $guard$;
update public.core_posts p set payload=x.after,deadline_at=x.deadline,created_at=x.created,
 version=p.version+1 from jsonb_to_recordset('{data}'::jsonb)
 x(id uuid,after jsonb,deadline timestamptz,created timestamptz) where p.id=x.id;
update public.core_profiles p set display_name=x.name,badge=x.badge,
 bio='校園情境示範人物，僅供體驗。' from jsonb_to_recordset('{people}'::jsonb)
 x(id uuid,old_name text,name text,badge text) where p.id=x.id and p.display_name=x.old_name;
commit;
select '130 identified development posts refreshed' as result;
"""
    (STATE / "refresh.sql").write_text(sql, encoding="utf-8")
    (STATE / "updates.json").write_text(
        json.dumps(updates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Prepared 130 reviewed updates, preserving memberships and original request payloads.")


if __name__ == "__main__":
    main()
