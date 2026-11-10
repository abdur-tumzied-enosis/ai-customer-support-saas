from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase
from orders.models import OrderSettings
from orders.setup import OrderSettingsExtract, ExtractedZone, extract_order_settings

User = get_user_model()
URL = "/api/orders/settings/"


class SettingsConfirmedTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-confirmed")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		self.url = f"{URL}?project_id={self.project.id}"
		self.client = APIClient()
		self.client.force_authenticate(user=self.admin)

	def confirmed_at(self):
		return OrderSettings.objects.get(project=self.project).settings_confirmed_at

	def test_saving_zones_sets_confirmed_at(self):
		r = self.client.patch(self.url, {"zones": [{"name": "Dhaka", "charge": "60"}]}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertIsNotNone(self.confirmed_at())

	def test_saving_payment_methods_channels_or_emails_sets_confirmed_at(self):
		bodies = [
			{"payment_methods": [{"label": "Cash", "default": True}]},
			{"channels": ["facebook"]},
			{"notification_emails": ["ops@x.test"]},
		]
		for body in bodies:
			OrderSettings.objects.filter(project=self.project).update(settings_confirmed_at=None)
			r = self.client.patch(self.url, body, format="json")
			self.assertEqual(r.status_code, 200, r.content)
			self.assertIsNotNone(self.confirmed_at(), body)

	def test_saving_an_unrelated_setting_does_not_set_confirmed_at(self):
		r = self.client.patch(self.url, {"draft_expiry_hours": 24}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertIsNone(self.confirmed_at())

	def test_disabling_orders_keeps_confirmed_at(self):
		OrderSettings.objects.create(project=self.project, enabled=True, settings_confirmed_at="2026-11-07T10:00:00Z")
		r = self.client.post(f"/api/orders/disable/?project_id={self.project.id}")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertIsNotNone(self.confirmed_at())

	def test_get_returns_confirmed_at(self):
		r = self.client.get(self.url)
		self.assertIsNone(r.json()["data"]["settings_confirmed_at"])
		self.client.patch(self.url, {"zones": [{"name": "Dhaka", "charge": "60"}]}, format="json")
		r = self.client.get(self.url)
		self.assertIsNotNone(r.json()["data"]["settings_confirmed_at"])


class SetupJobDoesNotConfirmTest(TestCase):
	def test_setup_job_leaves_confirmed_at_empty(self):
		project = Project.objects.create(name="P", slug="p-setup-confirmed")
		kb = KnowledgeBase.objects.create(project=project, name="Products", vector_collection="vc-setup-confirmed")
		Document.objects.create(kb=kb, source_type="url", page_url="https://shop.test/d", markdown_content="Dhaka 60 taka.")
		OrderSettings.objects.create(project=project)
		llm = MagicMock()
		llm.with_structured_output.return_value = llm
		raw = MagicMock(usage_metadata={"input_tokens": 1, "output_tokens": 1})
		llm.invoke.return_value = {"raw": raw, "parsed": OrderSettingsExtract(zones=[ExtractedZone(name="Dhaka", charge="60")])}
		with patch("orders.setup.get_llm", return_value=llm), patch("orders.setup.llm_pricing", return_value={"chat_in": {"cost_usd": None}, "chat_out": {"cost_usd": None}}), \
				patch("orders.setup.track_token_usage"):
			extract_order_settings(str(project.id), str(kb.id))
		self.assertIsNone(OrderSettings.objects.get(project=project).settings_confirmed_at)
