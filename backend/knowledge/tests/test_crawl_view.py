from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase


@patch("knowledge.views.crawl_website.delay")
class CrawlKbTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-crawl")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=user)

	def crawl(self, **extra):
		data = {"url": "https://example.com", "depth": 1, "project_id": str(self.project.id), **extra}
		return self.client.post("/api/kb/crawl/", data, format="json")

	def test_crawl_goes_to_chosen_kb(self, _delay):
		kb = KnowledgeBase.objects.create(project=self.project, name="Products")
		assert self.crawl(kb_id=str(kb.id)).status_code == 201
		assert Document.objects.get().kb_id == kb.id

	def test_crawl_without_kb_id_uses_default(self, _delay):
		assert self.crawl().status_code == 201
		assert Document.objects.get().kb.is_default

	def test_crawl_rejects_unknown_kb(self, _delay):
		assert self.crawl(kb_id="not-a-uuid").status_code == 404
		assert not Document.objects.exists()

	def test_crawl_with_malformed_project_id_is_403(self, _delay):
		assert self.crawl(project_id="not-a-uuid").status_code == 403
