"""Recognize a user's full-day or clock-time availability without inventing a two-hour window."""

from datetime import UTC, datetime, timedelta
import re
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

_CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_DAY_PART = r"(?:今天|今晚|今早|今日|明天|明晚|明早|明日)"
_PERIOD_PART = r"(?:早上|上午|中午|下午|傍晚|晚上|凌晨|半夜)"
_HOUR_TOKEN = r"(?:\d{1,2}|十[一二]?|[一二兩三四五六七八九])"
_MIN_TOKEN = r"(?:\d{1,2}|半)"


def _parse_hour(token: str) -> int | None:
    if token.isdigit():
        val = int(token)
        return val if 0 <= val <= 23 else None
    if token == "十":
        return 10
    if token.startswith("十"):
        tail = _CN_NUM.get(token[1:])
        return 10 + tail if tail is not None else None
    return _CN_NUM.get(token)


def _parse_minute(token: str | None) -> int | None:
    if token is None:
        return 0
    if token == "半":
        return 30
    if token.isdigit():
        val = int(token)
        return val if 0 <= val <= 59 else None
    return None


def _apply_period(hour: int, period: str | None) -> int:
    if period in {"下午", "傍晚", "晚上", "今晚", "明晚"} and hour < 12:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    if period in {"凌晨", "半夜"} and hour == 12:
        return 0
    return hour


def extract_clock_window(
    text: str, now: datetime | None = None
) -> tuple[int, int | None, str] | None:
    """Parse explicit clock time / time range (Taipei time) into (delay_minutes, duration_minutes, quote)."""
    base = (now or datetime.now(UTC)).astimezone(TAIPEI)
    range_pat = re.compile(
        rf"(?P<day>{_DAY_PART})?\s*(?P<p1>{_PERIOD_PART})?\s*"
        rf"(?P<h1>{_HOUR_TOKEN})\s*(?:點|時|:|：)\s*(?P<m1>{_MIN_TOKEN})?\s*(?:分|鐘)?\s*"
        rf"(?:到|至|~|～|-|—)\s*"
        rf"(?P<p2>{_PERIOD_PART})?\s*(?P<h2>{_HOUR_TOKEN})\s*(?:點|時|:|：)\s*(?P<m2>{_MIN_TOKEN})?\s*(?:分|鐘)?"
    )
    single_pat = re.compile(
        rf"(?:(?P<day>{_DAY_PART})\s*)?(?P<p1>{_PERIOD_PART})\s*"
        rf"(?P<h1>{_HOUR_TOKEN})\s*(?:點|時|:|：)\s*(?P<m1>{_MIN_TOKEN})?\s*(?:分|鐘)?"
    )
    matches = list(range_pat.finditer(text))
    is_range = True
    if not matches:
        matches = list(single_pat.finditer(text))
        is_range = False
    for match in reversed(matches):
        clause_start = max((text.rfind(sep, 0, match.start()) for sep in "，。；\n"), default=-1) + 1
        clause_end = min(
            (idx for sep in "，。；\n" if (idx := text.find(sep, match.end())) != -1),
            default=len(text),
        )
        clause = text[clause_start:clause_end]
        if re.search(r"不是|不要|不能|不行|無法|不想|不方便|如果|假如|[?？]", clause):
            continue
        h1 = _parse_hour(match.group("h1"))
        m1 = _parse_minute(match.group("m1"))
        if h1 is None or m1 is None:
            continue
        day_token = match.group("day") or ""
        p1 = match.group("p1") or (
            "今晚" if day_token == "今晚" else "明晚" if day_token == "明晚" else None
        )
        # Skip ambiguous bare 24h-less numbers without period unless colon format was used.
        if p1 is None and ":" not in match.group(0) and "：" not in match.group(0) and h1 <= 12:
            if not is_range or not match.group("p2"):
                continue
        start_hour = _apply_period(h1, p1)
        target_date = base.date() + timedelta(
            days=1 if day_token in {"明天", "明晚", "明早", "明日"} else 0
        )
        start_dt = datetime(
            target_date.year, target_date.month, target_date.day, start_hour, m1, tzinfo=TAIPEI
        )
        if is_range:
            h2 = _parse_hour(match.group("h2"))
            m2 = _parse_minute(match.group("m2"))
            if h2 is None or m2 is None:
                continue
            p2 = match.group("p2") or p1
            end_hour = _apply_period(h2, p2)
            end_dt = datetime(
                target_date.year, target_date.month, target_date.day, end_hour, m2, tzinfo=TAIPEI
            )
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            if not day_token and end_dt <= base:
                start_dt += timedelta(days=1)
                end_dt += timedelta(days=1)
            if end_dt <= base:
                continue
            effective_start = max(base, start_dt)
            delay = max(0, min(10065, round((effective_start - base).total_seconds() / 60)))
            duration = max(15, min(1440, round((end_dt - effective_start).total_seconds() / 60)))
            return (delay, duration, match.group(0).strip())
        if not day_token and start_dt <= base - timedelta(minutes=5):
            start_dt += timedelta(days=1)
        delay = max(0, min(10065, round((start_dt - base).total_seconds() / 60)))
        return (delay, None, match.group(0).strip())
    return None



def all_day(text: str) -> tuple[str, str] | None:
    matches = list(
        re.finditer(
            r"(?:今天|明天)?(?:一整天|整天|全天|隨時)(?:都)?(?:可以|有空|可配合|方便)?", text
        )
    )
    for match in reversed(matches):
        before = text[max(0, match.start() - 8) : match.start()]
        scope = re.split(r"但|不過|可是|[，。；\n]", before)[-1]
        if re.search(r"(?:不是|並非|不能|不行|無法|不想|不方便)\s*$|假如|如果|昨天|前天", scope):
            continue
        tail = text[match.end() :]
        if re.search(
            r"改時間|改成|改為|只能|結束|(?:\d+|半|一|兩|二|三|四|五|六|七|八|九|十)\s*(?:分鐘|小時|鐘頭)",
            tail,
        ):
            return None
        clause = re.split(r"[，。；\n]", text[: match.end()])[-1]
        return ("tomorrow" if "明天" in clause else "today", match.group())
    return None


def participation_preference(text: str) -> str | None:
    preference = None
    for clause in re.split(r"[，。；\n]", text):
        if re.search(r"朋友|同學|室友|對方|他們|她們|假如|如果|[?？]", clause):
            continue
        if re.search(r"不想待|不想留|不要待|不待|不留", clause):
            preference = None
        elif re.search(
            r"我(?:願意|可以|負責|會)(?:幫忙|去|幫大家)?(?:取餐|拿餐|下樓拿|出去拿)", clause
        ):
            preference = None
        elif re.search(
            r"不想出門|不方便出門|不想去拿|不跑腿|(?:待|留|等)在(?:宿舍|家|原地|這)|就在這邊取餐",
            clause,
        ):
            preference = "receive"
    return preference
