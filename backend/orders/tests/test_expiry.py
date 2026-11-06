from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders.models import Order, OrderSettings, OrderStatusHistory
from orders.tasks import expire_stale_drafts


class DraftExpiryTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-expiry")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-exp")
		self.settings = OrderSettings.objects.create(project=self.project, draft_expiry_hours=48)
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="e1")
		self.conv_old = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		self.conv_new = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)

	def draft(self, conv, hours_old):
		order = Order.objects.create(project=self.project, conversation=conv, channel="facebook", currency="BDT")
		# auto_now would overwrite updated_at on save, so set it directly
		Order.objects.filter(pk=order.pk).update(updated_at=timezone.now() - timedelta(hours=hours_old))
		return order

	def test_stale_draft_abandoned(self):
		order = self.draft(self.conv_old, hours_old=49)
		expire_stale_drafts()
		order.refresh_from_db()
		self.assertEqual(order.status, "abandoned")
		self.assertTrue(OrderStatusHistory.objects.filter(
			order=order, actor_type="system", from_status="draft", to_status="abandoned").exists())

	def test_fresh_draft_kept(self):
		order = self.draft(self.conv_new, hours_old=1)
		expire_stale_drafts()
		order.refresh_from_db()
		self.assertEqual(order.status, "draft")

	def test_expiry_uses_project_hours(self):
		OrderSettings.objects.filter(pk=self.settings.pk).update(draft_expiry_hours=2)
		order = self.draft(self.conv_old, hours_old=3)
		expire_stale_drafts()
		order.refresh_from_db()
		self.assertEqual(order.status, "abandoned")
