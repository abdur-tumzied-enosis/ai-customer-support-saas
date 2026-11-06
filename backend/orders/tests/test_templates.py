import json
from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from orders.models import Order, OrderItem
from orders.templates import TemplateError, build_context, render, validate_json_template


class TemplateTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abcf")
		self.order = Order.objects.create(
			project=self.project, channel="self", currency="BDT", status="new", number=1001,
			customer_name="Rahim & Co", customer_phone="+8801712345678", customer_address='He said "hi"\nRoad 5',
			zone_name="Dhaka", total=Decimal("310.00"), zone_charge=Decimal("60.00"),
		)
		OrderItem.objects.create(
			order=self.order, name="Shirt", product_external_id="S1", sku="SKU-1", quantity=1,
			unit_price=Decimal("250.00"), line_total=Decimal("250.00"), product_url="https://shop.example/s1",
		)

	def ctx(self):
		return build_context(self.order)

	def test_json_value_with_quotes_and_newline_is_valid_json(self):
		out = json.loads(render('{"address": {{ customer.address }}}', self.ctx(), "json"))
		self.assertEqual(out["address"], 'He said "hi"\nRoad 5')

	def test_url_value_encoded(self):
		self.assertEqual(render("/c?name={{ customer.name }}", self.ctx(), "url"), "/c?name=Rahim%20%26%20Co")

	def test_quoted_placeholder_rejected(self):
		with self.assertRaises(TemplateError) as cm:
			validate_json_template('{"name": "{{ customer.name }}"}')
		self.assertEqual(cm.exception.code, "invalid_body_template")

	def test_unknown_placeholder_rejected(self):
		with self.assertRaises(TemplateError) as cm:
			render("{{ customer.secret }}", self.ctx(), "json")
		self.assertEqual(cm.exception.code, "unknown_placeholder")

	def test_missing_value_raises_missing_value(self):
		self.order.number = None
		with self.assertRaises(TemplateError) as cm:
			render("{{ order.number }}", build_context(self.order), "json")
		self.assertEqual(cm.exception.code, "missing_value")

	def test_items_as_json_array(self):
		items = json.loads(render("{{ order.items }}", self.ctx(), "json"))
		self.assertEqual(items[0]["external_id"], "S1")
		self.assertEqual(items[0]["sku"], "SKU-1")
		self.assertEqual(items[0]["product_url"], "https://shop.example/s1")

	def test_items_in_url_rejected(self):
		with self.assertRaises(TemplateError):
			render("?items={{ order.items }}", self.ctx(), "url")

	def test_valid_json_template_accepted(self):
		validate_json_template('{"order": {{ order.number }}, "items": {{ order.items }}, "to": {{ customer.name }}}')
