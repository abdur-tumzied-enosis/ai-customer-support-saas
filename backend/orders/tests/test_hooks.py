from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders import hooks
from orders.models import Order, OrderAction, OrderItem, OrderSettings
from orders.notify import order_payload


class OrderHooksTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="Shop", slug="0123456789abcdef0123456789abd1")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-hook1")
		contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		OrderSettings.objects.create(project=self.project, enabled=True, notification_emails=["a@x.test", "b@x.test"])
		self.order = Order.objects.create(
			project=self.project, conversation=self.conv, channel="self", currency="BDT", status="new", number=1001,
			customer_name="Rahim", customer_phone="+8801712345678", customer_address="Road 1", zone_name="Dhaka",
			total=Decimal("310.00"),
		)
		OrderItem.objects.create(
			order=self.order, name="Shirt", product_external_id="S1", sku="SKU-1", quantity=1,
			unit_price=Decimal("250.00"), line_total=Decimal("250.00"), product_url="https://shop.example/s1",
		)

	def test_test_order_triggers_nothing(self):
		self.order.is_test = True
		self.order.save()
		with patch("orders.notify.queue_email") as email, patch("orders.hooks.emit_event") as emit, \
				patch("orders.actions.run_action_for_orders") as run:
			hooks.order_submitted(str(self.order.id))
			hooks.order_status_changed(str(self.order.id), "new", "shipped")
		email.assert_not_called()
		emit.assert_not_called()
		run.assert_not_called()

	def test_new_order_emails_each_address(self):
		with patch("orders.notify.queue_email") as email, patch("orders.hooks.emit_event"):
			hooks.order_submitted(str(self.order.id))
		self.assertEqual([c.args[0] for c in email.call_args_list], ["a@x.test", "b@x.test"])
		self.assertIn("#1001", email.call_args.args[1])

	def test_new_order_emitted_as_order_created(self):
		with patch("orders.notify.queue_email"), patch("orders.hooks.emit_event") as emit:
			hooks.order_submitted(str(self.order.id))
		project, event, data = emit.call_args.args
		self.assertEqual(project, self.project)
		self.assertEqual(event, "order.created")
		self.assertEqual(data["items"][0]["external_id"], "S1")
		self.assertEqual(data["items"][0]["sku"], "SKU-1")

	def test_auto_run_only_for_auto_actions(self):
		auto = OrderAction.objects.create(project=self.project, name="Auto", method="GET", url="https://c.example/a", auto_run=True)
		OrderAction.objects.create(project=self.project, name="Manual", method="GET", url="https://c.example/m")
		with patch("orders.notify.queue_email"), patch("orders.hooks.emit_event"), \
				patch("orders.actions.run_action_for_orders") as run:
			hooks.order_submitted(str(self.order.id))
		run.assert_called_once()
		self.assertEqual(run.call_args.args[0], auto)

	def test_status_change_emits_event(self):
		with patch("orders.hooks.emit_event") as emit:
			hooks.order_status_changed(str(self.order.id), "new", "shipped")
		_, event, data = emit.call_args.args
		self.assertEqual(event, "order.status_changed")
		self.assertEqual((data["from"], data["to"]), ("new", "shipped"))

	def test_payload_contains_product_url(self):
		self.assertEqual(order_payload(self.order)["items"][0]["product_url"], "https://shop.example/s1")
