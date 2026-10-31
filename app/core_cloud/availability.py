"""Recognize a user's full-day availability without inventing a two-hour window."""

import re


def all_day(text: str) -> tuple[str, str] | None:
    matches = list(
        re.finditer(r"(?:今天|明天)?(?:一整天|整天|全天)(?:都)?(?:可以|有空|可配合|方便)?", text)
    )
    for match in reversed(matches):
        before = text[max(0, match.start() - 8) : match.start()]
        if re.search(r"不是|不能|不行|無法|不想|不方便|假如|如果|昨天|前天", before):
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
