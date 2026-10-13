from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction

URL = "/api/kb/instructions/"


class InstructionApiToolTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-api-tool")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user)

	def _create(self, **extra):
		return self.client.post(URL, {
			"project_id": str(self.project.id), "label": "Orders", "message": "Use GET https://shop.com/o/{id}", **extra,
		}, format="json")

	def test_create_stores_secret_but_never_returns_it(self):
		resp = self._create(api_tool_enabled=True, api_domains=[{"domain": "shop.com", "auth_header": "X-Key", "secret": "k1"}])
		assert resp.status_code == 201, resp.content
		inst = resp.json()["data"]["instruction"]
		assert inst["api_tool_enabled"] is True
		assert inst["api_domains"] == [{"domain": "shop.com", "auth_header": "X-Key", "has_secret": True}]
		assert b"k1" not in resp.content
		assert CustomInstruction.objects.get(id=inst["id"]).api_domains[0]["secret"] == "k1"

		listing = self.client.get(URL, {"project_id": str(self.project.id)})
		assert b"k1" not in listing.content
		assert listing.json()["data"]["instructions"][0]["api_domains"][0]["has_secret"] is True

	def test_patch_blank_secret_keeps_it(self):
		inst_id = self._create(api_tool_enabled=True, api_domains=[{"domain": "shop.com", "auth_header": "X-Key", "secret": "k1"}]).json()["data"]["instruction"]["id"]
		resp = self.client.patch(f"{URL}{inst_id}/", {
			"label": "Orders v2", "api_domains": [{"domain": "shop.com", "auth_header": "X-Key", "secret": ""}],
		}, format="json")
		assert resp.status_code == 200, resp.content
		assert CustomInstruction.objects.get(id=inst_id).api_domains[0]["secret"] == "k1"

	def test_invalid_config_returns_400(self):
		assert self._create(api_tool_enabled=True, api_domains=[]).json()["error"]["code"] == "invalid_api_config"
		resp = self._create(api_domains=[{"domain": "127.0.0.1"}])
		assert resp.status_code == 400

	def test_default_is_disabled(self):
		inst = self._create().json()["data"]["instruction"]
		assert inst["api_tool_enabled"] is False and inst["api_domains"] == []
