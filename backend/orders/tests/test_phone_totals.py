from decimal import Decimal

from django.test import SimpleTestCase

from orders.phone import normalise_bd_phone
from orders.totals import MAX_LINE_QTY, collect_amount, compute_totals


class PhoneTest(SimpleTestCase):
	def test_normalises_common_forms(self):
		cases = {
			"+8801712345678": "01712345678",
			"8801712345678": "01712345678",
			"01712-345678": "01712345678",
			"(+880) 1712 345678": "01712345678",
			"০১৭১২৩৪৫৬৭৮": "01712345678",
			"0171234567": None,  # too short
			"02123456": None,  # not a mobile prefix
			"": None,
			None: None,
		}
		for raw, want in cases.items():
			with self.subTest(raw=raw):
				self.assertEqual(normalise_bd_phone(raw), want)


class TotalsTest(SimpleTestCase):
	def test_compute_totals_server_side(self):
		totals = compute_totals([(Decimal("250"), 2), (Decimal("100"), 1)], Decimal("60"))
		self.assertEqual(totals.subtotal, Decimal("600.00"))
		self.assertEqual(totals.total, Decimal("660.00"))

	def test_collect_amount_minus_advance(self):
		self.assertEqual(collect_amount(Decimal("660"), Decimal("100")), Decimal("560.00"))

	def test_quantity_cap_constant_is_100(self):
		self.assertEqual(MAX_LINE_QTY, 100)
