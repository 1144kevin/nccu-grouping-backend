"""Recognize a user's full-day availability without inventing a two-hour window."""

import re


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
