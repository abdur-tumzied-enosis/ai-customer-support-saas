from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase, Product
from orders.models import Order, OrderSettings
from orders.service import (
	OrderValidationError, apply_lines, get_or_create_draft, ordering_active, submit_order, update_customer,
)

ME = ("user", "u1")


def setup_world(t):
	t.project = Project.objects.create(name="P", slug="p-orders-svc")
	t.kb = KnowledgeBase.objects.create(project=t.project, name="Products")
	t.other_kb = KnowledgeBase.objects.create(project=t.project, name="Other")
	t.product = Product.objects.create(kb=t.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")
	t.settings = OrderSettings.objects.create(
		project=t.project, enabled=True, channels=["facebook"],
		zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
	)
	fb = CustomerContact.objects.create(project=t.project, platform="facebook", external_id="f1")
	t.conv = Conversation.objects.create(project=t.project, contact=fb, knowledge_base=t.kb)
	self_contact = CustomerContact.objects.create(project=t.project, platform="self", external_id="t1")
	t.test_conv = Conversation.objects.create(project=t.project, contact=self_contact, knowledge_base=t.kb)


def ready(t, conv=None, qty=2):
	order = get_or_create_draft(conv or t.conv, t.settings)
	apply_lines(order, [{"product_id": str(t.product.id), "quantity": qty}], t.settings)
	update_customer(order, {
		"customer_name": "Rahim", "customer_phone": "+8801712345678",
		"customer_address": "Road 1", "zone_name": "Dhaka",
	}, t.settings)
	return order


class ServiceTest(TestCase):
	def setUp(self):
		setup_world(self)

	def test_submit_twice_one_order(self):
		order = ready(self)
		first = submit_order(order, ME)
		second = submit_order(order, ME)
		self.assertEqual(first.number, second.number)
		self.assertEqual(Order.objects.filter(conversation=self.conv).count(), 1)

	def test_number_starts_at_1001(self):
		self.assertEqual(submit_order(ready(self), ME).number, 1001)

	def test_submit_rechecks_price(self):
		order = ready(self)
		Product.objects.filter(pk=self.product.pk).update(price=None, status="needs_info")
		with self.assertRaises(OrderValidationError) as cm:
			submit_order(order, ME)
		self.assertIn("needs_price", [p["code"] for p in cm.exception.problems])

	def test_unavailable_line_rejected(self):
		Product.objects.filter(pk=self.product.pk).update(available=False)
		order = get_or_create_draft(self.conv, self.settings)
		with self.assertRaises(OrderValidationError) as cm:
			apply_lines(order, [{"product_id": str(self.product.id), "quantity": 1}], self.settings)
		self.assertEqual(cm.exception.problems[0]["code"], "unavailable")

	def test_product_from_other_kb_rejected(self):
		order = get_or_create_draft(self.conv, self.settings)
		other = Product.objects.create(kb=self.other_kb, name="Hat", price=Decimal("100"), status="active")
		with self.assertRaises(OrderValidationError) as cm:
			apply_lines(order, [{"product_id": str(other.id), "quantity": 1}], self.settings)
		self.assertEqual(cm.exception.problems[0]["code"], "unknown_product")

	def test_zone_must_exist(self):
		order = get_or_create_draft(self.conv, self.settings)
		with self.assertRaises(ValidationError):
			update_customer(order, {"zone_name": "Nowhere"}, self.settings)

	def test_customer_unknown_key_rejected(self):
		order = get_or_create_draft(self.conv, self.settings)
		with self.assertRaises(ValidationError):
			update_customer(order, {"favourite_colour": "red"}, self.settings)

	def test_test_chat_marks_is_test(self):
		self.assertTrue(get_or_create_draft(self.test_conv, self.settings).is_test)
		self.assertFalse(get_or_create_draft(self.conv, self.settings).is_test)

	def test_ordering_active_matrix(self):
		s = self.settings
		OrderSettings.objects.filter(pk=s.pk).update(enabled=True, channels=["facebook"], test_mode=False)
		self.assertIsNotNone(ordering_active(self.conv))  # enabled, channel listed
		self.assertIsNone(ordering_active(self.test_conv))  # enabled, self not listed, no test mode
		OrderSettings.objects.filter(pk=s.pk).update(channels=[])
		self.assertIsNone(ordering_active(self.conv))  # enabled, channel not listed
		OrderSettings.objects.filter(pk=s.pk).update(enabled=False, test_mode=True)
		self.assertIsNotNone(ordering_active(self.test_conv))  # test mode on the self chat
		self.assertIsNone(ordering_active(self.conv))  # feature off for real channels
		OrderSettings.objects.filter(pk=s.pk).update(test_mode=False)
		self.assertIsNone(ordering_active(self.test_conv))  # feature off


class ConcurrentSubmitTest(TransactionTestCase):
	def setUp(self):
		setup_world(self)

	def test_concurrent_submit_one_number(self):
		order = ready(self)
		barrier = Barrier(2)
		results, errors = [], []

		def run():
			try:
				barrier.wait()
				results.append(submit_order(order, ME).number)
			except Exception as e:  # collected and asserted below, not swallowed
				errors.append(e)
			finally:
				connection.close()

		with patch("orders.hooks.order_submitted.delay"):
			threads = [Thread(target=run) for _ in range(2)]
			for t in threads:
				t.start()
			for t in threads:
				t.join()
		self.assertEqual(errors, [])
		self.assertEqual(len(set(results)), 1)
		self.assertEqual(Order.objects.filter(number__isnull=False).count(), 1)
