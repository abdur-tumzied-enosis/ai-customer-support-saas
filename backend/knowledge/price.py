import re
import unicodedata
from decimal import Decimal

BENGALI_DIGITS = str.maketrans("".join(chr(0x09E6 + i) for i in range(10)), "0123456789")
CURRENCY = re.compile(r"৳|Tk|BDT|টাকা|/-", re.IGNORECASE)
AMOUNT = re.compile(r"\d[\d,]*(?:\.\d{1,2})?")
# "মূল্য" means plain "price", so it is not a hint. Hints and text are both NFC (য় has two encodings).
REGULAR_HINTS = ("regular", "reg.", "was", "original", unicodedata.normalize("NFC", "নিয়মিত"))


# ponytail: keyword heuristic for sale vs regular, not a grammar; upgrade if a real feed breaks it
def parse_price(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    text = CURRENCY.sub(" ", unicodedata.normalize("NFC", raw).translate(BENGALI_DIGITS))
    prev_end = 0
    for m in AMOUNT.finditer(text):
        # keyword must sit between the previous amount and this one
        gap = text[prev_end:m.start()].lower()
        prev_end = m.end()
        if any(hint in gap for hint in REGULAR_HINTS):
            continue
        value = Decimal(m.group().replace(",", ""))
        return value if value > 0 else None
    return None
