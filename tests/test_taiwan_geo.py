from types import SimpleNamespace
from app.core_cloud.taiwan_geo import TAIWAN_COUNTIES, filter_posts_by_regions, infer_taiwan_county


def test_taiwan_counties_has_all_22():
    assert len(TAIWAN_COUNTIES) == 22


def test_filter_posts_by_regions_uses_real_coordinates():
    taipei_post = SimpleNamespace(
        id="1",
        location=SimpleNamespace(name="未知店名", latitude=25.0330, longitude=121.5654),
    )
    taichung_post = SimpleNamespace(
        id="2",
        location=SimpleNamespace(name="台北牛肉麵", latitude=24.1788, longitude=120.6467),
    )
    assert filter_posts_by_regions([taipei_post, taichung_post], []) == [taipei_post, taichung_post]
    assert filter_posts_by_regions([taipei_post, taichung_post], ["臺北市"]) == [taipei_post]
    assert filter_posts_by_regions([taipei_post, taichung_post], ["臺中市"]) == [taichung_post]


def test_infer_taiwan_county_from_real_coordinates():
    # NCCU -> 臺北市
    assert infer_taiwan_county(24.9875, 121.5760) == "臺北市"
    # Taipei 101 -> 臺北市
    assert infer_taiwan_county(25.0330, 121.5654) == "臺北市"
    # Banqiao -> 新北市
    assert infer_taiwan_county(25.0143, 121.4636) == "新北市"
    # Zhonghe -> 新北市
    assert infer_taiwan_county(24.9990, 121.4950) == "新北市"
    # Tamsui -> 新北市
    assert infer_taiwan_county(25.1720, 121.4430) == "新北市"
    # Taoyuan Zhongli -> 桃園市
    assert infer_taiwan_county(24.9537, 121.2256) == "桃園市"
    # Hsinchu City vs County
    assert infer_taiwan_county(24.8039, 120.9647) == "新竹市"
    assert infer_taiwan_county(24.8397, 121.0110) == "新竹縣"
    # Taichung -> 臺中市
    assert infer_taiwan_county(24.1788, 120.6467) == "臺中市"
    # Chiayi City vs County
    assert infer_taiwan_county(23.4801, 120.4491) == "嘉義市"
    assert infer_taiwan_county(23.4589, 120.2929) == "嘉義縣"
    # Tainan / Kaohsiung / Hualien
    assert infer_taiwan_county(22.9997, 120.2270) == "臺南市"
    assert infer_taiwan_county(22.6660, 120.3025) == "高雄市"
    assert infer_taiwan_county(23.9769, 121.6044) == "花蓮縣"
    # Offshore islands
    assert infer_taiwan_county(23.5711, 119.5793) == "澎湖縣"
    assert infer_taiwan_county(24.4367, 118.3186) == "金門縣"
    assert infer_taiwan_county(26.1505, 119.9293) == "連江縣"
    # Outside Taiwan -> None
    assert infer_taiwan_county(35.6762, 139.6503) is None
