from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction, KnowledgeBase
from orders.models import OrderSettings

User = get_user_model()
URL = "/api/orders/settings/"


class SettingsApiTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-settings")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.url = f"{URL}?project_id={self.project.id}"
		self.client = APIClient()

	def test_patch_zones_saves(self):
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(self.url, {"zones": [{"name": "Dhaka", "charge": "60"}, {"name": "Outside", "charge": None}]}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		saved = OrderSettings.objects.get(project=self.project).zones
		self.assertEqual(saved[0], {"name": "Dhaka", "charge": "60"})
		self.assertIsNone(saved[1]["charge"])

	def test_zone_charge_negative_rejected(self):
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(self.url, {"zones": [{"name": "Dhaka", "charge": "-5"}]}, format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_zones")

	def test_payment_needs_one_default(self):
		self.client.force_authenticate(user=self.admin)
		two = [{"label": "A", "default": True}, {"label": "B", "default": True}]
		none = [{"label": "A", "default": False}]
		for methods in (two, none):
			r = self.client.patch(self.url, {"payment_methods": methods}, format="json")
			self.assertEqual(r.status_code, 400, r.content)
			self.assertEqual(r.json()["error"]["code"], "invalid_payment_methods")

	def test_member_cannot_patch(self):
		self.client.force_authenticate(user=self.member)
		r = self.client.patch(self.url, {"zones": [{"name": "Dhaka", "charge": "60"}]}, format="json")
		self.assertEqual(r.status_code, 403, r.content)

	def test_prompt_preview_includes_kb_order_instructions(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-prev")
		CustomInstruction.objects.create(project=self.project, knowledge_base=kb, label="Landmark", message="Ask for a landmark.", scope="order")
		self.client.force_authenticate(user=self.member)
		r = self.client.get(self.url)
		self.assertIn("Ask for a landmark.", r.json()["data"]["prompt_preview"])

	def test_prompt_preview_contains_zone_name(self):
		OrderSettings.objects.create(project=self.project, zones=[{"name": "Dhaka", "charge": "60"}])
		self.client.force_authenticate(user=self.member)
		r = self.client.get(self.url)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertIn("Dhaka", r.json()["data"]["prompt_preview"])
