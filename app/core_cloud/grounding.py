"""Verify quoted user evidence before accepting model-proposed numbers/roles.

This is a narrow validator after LLM interpretation, not a second semantic agent.
It has no database, tools, network or authority to consent or create a signal.
"""

import re

NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|[零〇一二兩三四五六七八九十百千萬半]+)"
DIGITS = {
    c: i
    for i, chars in enumerate(("零〇", "一", "二兩", "三", "四", "五", "六", "七", "八", "九"))
    for c in chars
}
FOREIGN_CURRENCY = r"美元|美金|USD|人民幣|CNY|RMB|日圓|日元|JPY|港幣|HKD|歐元|EUR|韓元|KRW|英鎊|GBP"


def personal_role_evidence(quote: str, original: str) -> bool:
    """Reject ambiguous speaker/request/question context, even outside a short quote."""
    for found in re.finditer(re.escape(quote), original):
        before = original[: found.start()]
        after = original[found.end() :]
        prefix = re.split(r"[，。；\n]", before)[-1]
        suffix = re.split(r"[，。；\n]", after)[0]
        clause = prefix + quote + suffix
        if re.search(
            r"希望有人|希望別人|找人|徵人|請人|請別人|請其他|有人願意|有人可以|"
            r"朋友|同學|室友|對方|他們|她們|(?:他|她)(?:說|會|能|願意|可以)|"
            r"[?？]|願不願意|應不應該|還沒決定|尚未決定|不確定|可能|也許|如果|假如|假設",
            clause,
        ):
            continue
        return True
    return False


def integer(value: str) -> int | None:
    if re.fullmatch(r"[0-9]+(?:\.0+)?", value):
        return int(value.split(".")[0])
    if not value or any(c not in DIGITS and c not in "十百千萬" for c in value):
        return None
    if len(value) > 1 and all(c in DIGITS for c in value):
        # Digit-by-digit Chinese writing is ambiguous; never turn 一二〇 into zero.
        return None
    total = section = number = 0
    for char in value:
        if char in DIGITS:
            number = DIGITS[char]
        elif char == "萬":
            total += (section + number or 1) * 10000
            section = number = 0
        else:
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            number = 0
    return total + section + number


def money_supported(value: int, quote: str, *, basis: str = "amount") -> bool:
    if (
        value == 0
        and re.search(r"免費|零元|不用錢|不收費", quote)
        and not re.search(
            r"(?:不是|不|非|沒有|並非)[^，。；\n]{0,3}(?:免費|零元|不用錢|不收費)", quote
        )
    ):
        return True
    quote = quote.replace(",", "")
    gap = r"[^0-9零〇一二兩三四五六七八九十百千萬半，。；\n]{0,8}"
    if basis == "budget":
        pattern = (
            rf"(?:預算|上限|最多|不超過){gap}({NUMBER})|({NUMBER})\s*(?:元|塊)?\s*(?:以內|以下)"
            rf"|(?:我)?(?:可以|願意|能夠|能)\s*(?:付|出|支付|負擔)\s*(?:NT\$|TWD|\$)?\s*({NUMBER})"
        )
    elif basis == "cost":
        pattern = rf"(?:每人|每個人|一人|人均|每份|一份){gap}({NUMBER})"
    else:
        pattern = rf"({NUMBER})\s*(?:元|塊)|(?:NT\$|TWD|預算|每人|每份|一份|人均){gap}({NUMBER})"
    amounts = [
        integer(item)
        for match in re.finditer(pattern, quote, re.IGNORECASE)
        for group, item in enumerate(match.groups(), 1)
        if item and not re.search(r"(?:不是|非|並非|不只)\s*$", quote[: match.start(group)])
    ]
    return value in amounts


def explicit_budget(original: str):
    """Recover a stated budget, including spoken '我可以付$200', never a price/fee."""
    found = None
    for clause in re.split(r"[，。；\n]", original):
        if not re.search(
            r"預算|上限|最多|不超過|(?:可以|願意|能夠|能)\s*(?:付|出|支付|負擔)", clause
        ):
            continue
        if re.search(r"未知|不知道|不確定|還沒決定|再說|沒有上限|不限預算", clause):
            found = None
            continue
        if re.search(FOREIGN_CURRENCY, clause, re.IGNORECASE) or re.search(
            r"朋友|同學|室友|對方|他們|她們|(?:他|她)的|如果|假如|假設|不是|不要|不能|沒辦法|[?？]|多付|跑腿費|加價",
            clause,
        ):
            continue
        for token in re.findall(NUMBER, clause):
            amount = integer(token)
            if (
                amount is not None
                and 0 <= amount <= 100000
                and money_supported(amount, clause, basis="budget")
            ):
                found = (amount, clause[:160])
    return found


def minutes_supported(value: int, quote: str, *, start: bool) -> bool:
    if start and value == 0 and re.search(r"現在|立刻|馬上|立即", quote):
        return True
    if start and not re.search(r"後|再過|再等|過了", quote):
        return False
    for match in re.finditer(rf"({NUMBER})\s*(個半|個)?\s*(分鐘|小時|鐘頭|分|天|日)(半)?", quote):
        number, middle, unit, half = match.groups()
        amount = 0.5 if number == "半" else float(number) if number.isascii() else integer(number)
        if amount is None:
            continue
        amount += 0.5 if middle == "個半" or half else 0
        factor = 60 if unit in ("小時", "鐘頭") else 1440 if unit in ("天", "日") else 1
        if amount * factor == value:
            return True
    return False


def denied_roles(quote: str) -> set[str]:
    patterns = {
        "order": "下單|訂餐|訂購|點餐",
        "pickup": "取餐|拿餐|領餐|帶餐",
        "coupon": "優惠券|折價券|折扣碼",
        "share": "分食|分享",
    }
    negative = r"(?:不方便|不願意|不能|無法|不要|不想|沒辦法|不)[^，。；、\n但願可]{0,8}"
    return {
        role
        for role, action in patterns.items()
        if re.search(negative + "(?:" + action + ")", quote)
    }


def supported_roles(quote: str) -> set[str]:
    patterns = {
        "order": "下單|訂餐|訂購|點餐|幫忙買",
        "pickup": "取餐|拿餐|領餐|帶餐|幫拿",
        "coupon": "優惠券|折價券|折扣碼",
        "share": "分食|分享|一起吃|一起分|湊單|合買",
    }
    roles = {role for role, pattern in patterns.items() if re.search(pattern, quote)}
    if "pickup" in roles and not re.search(
        r"願意|可以|能幫|會幫|順便幫|順路幫|負責|幫忙|我會.*取餐|我去.*取餐", quote
    ):
        roles.remove("pickup")
    return roles
