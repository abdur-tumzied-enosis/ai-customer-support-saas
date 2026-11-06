from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders.models import Order, OrderAction, OrderActionRun, OrderSettings


class ActionModelTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abce")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-act1")
		contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		self.order = Order.objects.create(project=self.project, conversation=self.conv, channel="self", status="new", number=1001)

	def test_action_name_unique_per_project(self):
		OrderAction.objects.create(project=self.project, name="Steadfast", method="POST", url="https://a.example/x")
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				OrderAction.objects.create(project=self.project, name="Steadfast", method="GET", url="https://a.example/y")

	def test_run_per_order_allowed_many(self):
		action = OrderAction.objects.create(project=self.project, name="Courier", method="POST", url="https://a.example/x")
		OrderActionRun.objects.create(action=action, order=self.order, status="failed")
		OrderActionRun.objects.create(action=action, order=self.order, status="succeeded")
		self.assertEqual(self.order.action_runs.count(), 2)

	def test_live_lookup_defaults_empty(self):
		settings = OrderSettings.objects.create(project=self.project)
		self.assertEqual(settings.live_lookup, {})
