"""Geographic county/city lookup for Taiwan based on post coordinates."""

from __future__ import annotations

import math

TAIWAN_COUNTIES: tuple[str, ...] = (
    "臺北市",
    "新北市",
    "基隆市",
    "桃園市",
    "新竹市",
    "新竹縣",
    "苗栗縣",
    "臺中市",
    "彰化縣",
    "南投縣",
    "雲林縣",
    "嘉義市",
    "嘉義縣",
    "臺南市",
    "高雄市",
    "屏東縣",
    "宜蘭縣",
    "花蓮縣",
    "臺東縣",
    "澎湖縣",
    "金門縣",
    "連江縣",
)

_COUNTY_SET = frozenset(TAIWAN_COUNTIES)

# Multi-anchor points (major districts/townships) per county for accurate Haversine matching.
_COUNTY_ANCHORS: dict[str, tuple[tuple[float, float], ...]] = {
    "臺北市": (
        (25.0478, 121.5170),  # 中正/台北車站
        (25.0330, 121.5654),  # 信義
        (25.0800, 121.5600),  # 內湖/大直
        (25.1360, 121.5060),  # 北投
        (24.9875, 121.5760),  # 文山/政大
        (25.0550, 121.6070),  # 南港
    ),
    "新北市": (
        (25.0143, 121.4636),  # 板橋
        (25.0410, 121.4500),  # 新莊
        (25.0620, 121.4870),  # 三重
        (24.9990, 121.4950),  # 中和
        (24.9680, 121.5410),  # 新店
        (25.1720, 121.4430),  # 淡水
        (25.0680, 121.6620),  # 汐止
        (25.1090, 121.8110),  # 瑞芳
        (24.9360, 121.3730),  # 三峽
    ),
    "基隆市": ((25.1283, 121.7419), (25.1500, 121.7700)),
    "桃園市": (
        (24.9936, 121.3010),  # 桃園區
        (24.9537, 121.2256),  # 中壢
        (25.0560, 121.2130),  # 大園/機場
        (24.8640, 121.2130),  # 龍潭
        (24.8050, 121.3510),  # 復興
    ),
    "新竹市": ((24.8039, 120.9647), (24.7900, 120.9900)),
    "新竹縣": ((24.8397, 121.0110), (24.7390, 121.0870), (24.6750, 121.2150)),
    "苗栗縣": ((24.5602, 120.8214), (24.6870, 120.9030), (24.4390, 120.6540)),
    "臺中市": (
        (24.1477, 120.6736),  # 中區/西區
        (24.1788, 120.6467),  # 西屯/逢甲
        (24.2530, 120.7190),  # 豐原
        (24.2650, 120.5730),  # 清水
        (24.0950, 120.6920),  # 大里
        (24.2550, 121.2450),  # 和平/梨山
    ),
    "彰化縣": ((24.0757, 120.5430), (23.9590, 120.5710), (24.0540, 120.4310)),
    "南投縣": ((23.9157, 120.6840), (23.9660, 120.9670), (23.7570, 120.6830)),
    "雲林縣": ((23.7092, 120.5430), (23.7080, 120.4310), (23.5700, 120.3040)),
    "嘉義市": ((23.4801, 120.4491),),
    "嘉義縣": ((23.4589, 120.2929), (23.5560, 120.4300), (23.5100, 120.8030)),
    "臺南市": (
        (22.9997, 120.2270),  # 東區/中西區
        (23.0250, 120.2170),  # 永康
        (23.3100, 120.3160),  # 新營
        (23.1800, 120.2460),  # 麻豆
    ),
    "高雄市": (
        (22.6273, 120.3014),  # 前金/苓雅
        (22.6660, 120.3025),  # 左營
        (22.7300, 120.3320),  # 楠梓
        (22.6260, 120.3580),  # 鳳山
        (22.7940, 120.2960),  # 岡山
        (22.8890, 120.4810),  # 旗山
    ),
    "屏東縣": (
        (22.6690, 120.4860),  # 屏東市
        (22.5510, 120.5480),  # 潮州
        (22.4660, 120.4480),  # 東港
        (22.0030, 120.7440),  # 恆春
    ),
    "宜蘭縣": ((24.7570, 121.7530), (24.6770, 121.7670), (24.5940, 121.8520)),
    "花蓮縣": ((23.9769, 121.6044), (23.7530, 121.4510), (23.3320, 121.3170)),
    "臺東縣": ((22.7583, 121.1444), (23.1010, 121.2170), (22.6160, 120.9960)),
    "澎湖縣": ((23.5711, 119.5793),),
    "金門縣": ((24.4367, 118.3186),),
    "連江縣": ((26.1505, 119.9293), (26.2240, 119.9980)),
}


def normalize_county_name(name: str) -> str:
    return name.strip().replace("台", "臺")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def infer_taiwan_county(latitude: float, longitude: float) -> str | None:
    """Infer the Taiwan county/city from real GPS coordinates."""
    if not (21.5 <= latitude <= 26.5 and 118.0 <= longitude <= 122.5):
        return None

    # Offshore islands first
    if 24.25 <= latitude <= 24.65 and 118.10 <= longitude <= 118.60:
        return "金門縣"
    if 25.85 <= latitude <= 26.50 and 119.80 <= longitude <= 120.65:
        return "連江縣"
    if 23.10 <= latitude <= 23.90 and 119.25 <= longitude <= 119.80:
        return "澎湖縣"

    # Compact/enclave cities checked with tighter boxes before nearest-anchor
    if 25.09 <= latitude <= 25.21 and 121.68 <= longitude <= 121.82:
        return "基隆市"
    if 24.76 <= latitude <= 24.822 and 120.90 <= longitude <= 121.015:
        return "新竹市"
    if 23.45 <= latitude <= 23.51 and 120.41 <= longitude <= 120.49:
        return "嘉義市"

    best_county: str | None = None
    best_dist = float("inf")
    for county, anchors in _COUNTY_ANCHORS.items():
        if county in ("金門縣", "連江縣", "澎湖縣"):
            continue
        for a_lat, a_lng in anchors:
            d = _haversine_km(latitude, longitude, a_lat, a_lng)
            if d < best_dist:
                best_dist = d
                best_county = county
    return best_county


def selected_regions_from_tags(preferred_tags: list[str]) -> set[str]:
    regions: set[str] = set()
    for tag in preferred_tags:
        norm = normalize_county_name(tag)
        if norm in _COUNTY_SET:
            regions.add(norm)
    return regions


def filter_posts_by_regions(posts: list, preferred_tags: list[str]) -> list:
    regions = selected_regions_from_tags(preferred_tags)
    if not regions:
        return posts
    filtered = []
    for post in posts:
        county = infer_taiwan_county(post.location.latitude, post.location.longitude)
        if county in regions:
            filtered.append(post)
    return filtered
