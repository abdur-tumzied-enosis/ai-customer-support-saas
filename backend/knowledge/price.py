import re
from decimal import Decimal

BENGALI_DIGITS = str.maketrans("".join(chr(0x09E6 + i) for i in range(10)), "0123456789")
CURRENCY = re.compile(r"৳|Tk|BDT|টাকা|/-", re.IGNORECASE)
AMOUNT = re.compile(r"\d[\d,]*(?:\.\d{1,2})?")
REGULAR_HINTS = ("regular", "reg.", "was", "original", "মূল্য")


# ponytail: keyword heuristic for sale vs regular, not a grammar; upgrade if a real feed breaks it
def parse_price(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    text = CURRENCY.sub(" ", raw.translate(BENGALI_DIGITS))
    for m in AMOUNT.finditer(text):
        before = text[max(0, m.start() - 20):m.start()].lower()
        if any(hint in before for hint in REGULAR_HINTS):
            continue
        value = Decimal(m.group().replace(",", ""))
        return value if value > 0 else None
    return None
