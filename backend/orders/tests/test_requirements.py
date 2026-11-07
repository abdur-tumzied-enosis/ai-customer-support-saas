from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact, Widget
from knowledge.models import Document, KnowledgeBase, Product
from orders.models import Order, OrderSettings
from orders.requirements import check

User = get_user_model()


class RequirementsTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-req")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-req")
		self.client = APIClient()
		self.client.force_authenticate(user=self.admin)
		self.q = f"?project_id={self.project.id}"

	def add_channel(self):
		Widget.objects.create(project=self.project, name="Site", knowledge_base=self.kb, is_active=True)

	def add_source(self):
		Document.objects.create(kb=self.kb, source_type="url", page_url="https://shop.test/p1", markdown_content="Shirts 250")

	def add_active_product(self):
		Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")

	def test_channel_missing_is_hard_fail(self):
		items = {i["key"]: i for i in check(self.project)}
		self.assertEqual(items["channel"]["status"], "fail")
		self.assertTrue(items["channel"]["hard"])

	def test_products_alone_count_as_product_information(self):
		# a project that already has products (added by hand or from a sheet) is not blocked by a missing document
		self.add_product_without_source()
		items = {i["key"]: i for i in check(self.project)}
		self.assertEqual(items["product_source"]["status"], "ok")

	def add_product_without_source(self):
		Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="needs_info")

	def test_enable_blocked_without_product_source(self):
		self.add_channel()
		r = self.client.post("/api/orders/enable/" + self.q)
		self.assertEqual(r.status_code, 409, r.content)
		self.assertEqual(r.json()["error"]["code"], "requirements_unmet")

	def test_enable_blocked_without_active_product(self):
		self.add_channel()
		self.add_source()
		Product.objects.create(kb=self.kb, name="Cap", external_id="C1", status="needs_info")
		r = self.client.post("/api/orders/enable/" + self.q)
		self.assertEqual(r.status_code, 409, r.content)
		self.assertEqual(r.json()["error"]["code"], "no_active_product")

	def test_enable_succeeds(self):
		self.add_channel()
		self.add_source()
		self.add_active_product()
		r = self.client.post("/api/orders/enable/" + self.q)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertTrue(OrderSettings.objects.get(project=self.project).enabled)

	def test_disable_keeps_drafts(self):
		OrderSettings.objects.create(project=self.project, enabled=True, test_mode=True)
		contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="t1")
		conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		Order.objects.create(project=self.project, conversation=conv, channel="self", currency="BDT")
		r = self.client.post("/api/orders/disable/" + self.q)
		self.assertEqual(r.status_code, 200, r.content)
		settings = OrderSettings.objects.get(project=self.project)
		self.assertFalse(settings.enabled)
		self.assertFalse(settings.test_mode)
		self.assertTrue(Order.objects.filter(project=self.project, status="draft").exists())

	def test_test_mode_only_on_flag(self):
		r = self.client.post("/api/orders/test-mode/" + self.q, {}, format="json")
		self.assertEqual(r.status_code, 400, r.content)
		r = self.client.post("/api/orders/test-mode/" + self.q, {"on": True}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertTrue(OrderSettings.objects.get(project=self.project).test_mode)
