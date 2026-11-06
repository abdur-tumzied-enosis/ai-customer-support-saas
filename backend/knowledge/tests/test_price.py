import unittest
from decimal import Decimal
from knowledge.price import parse_price

CASES = [
    ("৳1,200", "1200"),
    ("1200/-", "1200"),
    ("Tk 1200", "1200"),
    ("BDT 1200", "1200"),
    ("১২০০", "1200"),
    ("৳১,২০০", "1200"),
    ("1,200.50", "1200.50"),
    ("৳1,200 (regular ৳1,500)", "1200"),       # regular price excluded
    ("Regular ৳1,500, now ৳1,200", "1200"),    # sale after regular
    ("Regular 1500 now 1200", "1200"),
    ("Price 1200 was 1500", "1200"),           # "was" sits between 1200 and 1500
    ("Regular 1,500, now 1,200", "1200"),      # same as the ৳ case with ৳ deleted
    ("মূল্য: ৳১২০০", "1200"),                  # মূল্য is plain "price", not a regular-price hint
    ("নিয়মিত ৳১৫০০, এখন ৳১২০০", "1200"),
    ("", None),
    (None, None),
    ("call for price", None),
    ("0", None),
]

class ParsePriceTest(unittest.TestCase):
    def test_cases(self):
        for raw, want in CASES:
            with self.subTest(raw=raw):
                got = parse_price(raw)
                self.assertEqual(got, Decimal(want) if want else None)
