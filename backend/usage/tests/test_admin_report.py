from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Project, User
from usage.models import UsageEvent


class AiCostAdminReportTests(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-admin-report")
		self.superuser = User.objects.create_superuser(username="su", email="su@example.com", password="pw-su")
		self.staff = User.objects.create_user(username="st", email="st@example.com", password="pw-st", is_staff=True)
		self.url = reverse("admin:usage_usageevent_report")

	def test_staff_non_superuser_denied(self):
		self.client.force_login(self.staff)
		self.assertEqual(self.client.get(self.url).status_code, 403)

	def test_superuser_sees_report(self):
		self.client.force_login(self.superuser)
		resp = self.client.get(self.url)
		self.assertEqual(resp.status_code, 200)
		self.assertIn("models", resp.context)
		self.assertIn("known_by_source", resp.context)

	def test_unknown_banner_shown_only_when_unpriced(self):
		self.client.force_login(self.superuser)
		self.assertNotContains(self.client.get(self.url), "Unpriced usage in this range")
		UsageEvent.objects.create(project=self.project, kind="chat_out", tokens=10, cost_usd=None, cost_source="unknown", model_name="x")
		self.assertContains(self.client.get(self.url), "Unpriced usage in this range")

	def test_changelist_superuser_only(self):
		self.client.force_login(self.staff)
		self.assertEqual(self.client.get(reverse("admin:usage_usageevent_changelist")).status_code, 403)
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:usage_usageevent_changelist")).status_code, 200)
