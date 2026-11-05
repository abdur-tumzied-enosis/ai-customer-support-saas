from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser

User = get_user_model()
URL = "/api/projects/settings/"


class ProjectSettingsTests(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="Shop", slug="shop")
		self.admin = User.objects.create_user(username="a@x.com", email="a@x.com", password="pw-123456")
		self.member = User.objects.create_user(username="m@x.com", email="m@x.com", password="pw-123456")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.client = APIClient()
		self.client.force_login(self.admin)

	def test_default_currency_is_bdt(self):
		r = self.client.get(URL, {"project_id": str(self.project.id)})
		assert r.status_code == 200, r.content
		assert r.json()["data"] == {"project_id": str(self.project.id), "name": "Shop", "currency": "BDT"}

	def test_patch_currency_saves(self):
		r = self.client.patch(URL, {"project_id": str(self.project.id), "currency": "USD", "name": "Shop 2"}, format="json")
		assert r.status_code == 200, r.content
		assert r.json()["data"]["currency"] == "USD"
		self.project.refresh_from_db()
		assert (self.project.currency, self.project.name) == ("USD", "Shop 2")

	def test_invalid_currency_rejected(self):
		r = self.client.patch(URL, {"project_id": str(self.project.id), "currency": "bd1"}, format="json")
		assert r.status_code == 400
		assert r.json()["error"]["code"] == "invalid_currency"
		self.project.refresh_from_db()
		assert self.project.currency == "BDT"

	def test_member_cannot_patch(self):
		self.client.force_login(self.member)
		r = self.client.patch(URL, {"project_id": str(self.project.id), "currency": "USD"}, format="json")
		assert r.status_code == 403
		assert r.json()["error"]["code"] == "access_denied"
