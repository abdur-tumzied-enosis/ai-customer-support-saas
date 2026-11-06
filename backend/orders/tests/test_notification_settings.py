from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import ProjectWebhook
from orders.models import OrderSettings

User = get_user_model()
SECRET = "whsec-secret-value"


class NotificationSettingsTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-notify")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		ProjectWebhook.objects.create(project=self.project, url="https://hooks.example.com/in", secret=SECRET, is_active=True)
		self.url = f"/api/orders/notifications/?project_id={self.project.id}"
		self.client = APIClient()

	def test_notification_emails_validated(self):
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(self.url, {"emails": ["not-an-email"]}, format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_emails")
		r = self.client.patch(self.url, {"emails": [f"u{i}@x.test" for i in range(11)]}, format="json")
		self.assertEqual(r.status_code, 400, r.content)
		r = self.client.patch(self.url, {"emails": ["shop@x.test"]}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(OrderSettings.objects.get(project=self.project).notification_emails, ["shop@x.test"])

	def test_webhook_summary_has_no_secret(self):
		self.client.force_authenticate(user=self.member)
		r = self.client.get(self.url)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertNotContains(r, SECRET)
		self.assertEqual(
			r.json()["data"]["webhook"],
			{"url": "https://hooks.example.com/in", "is_active": True, "has_secret": True},
		)

	def test_member_cannot_patch(self):
		self.client.force_authenticate(user=self.member)
		r = self.client.patch(self.url, {"emails": ["shop@x.test"]}, format="json")
		self.assertEqual(r.status_code, 403, r.content)
