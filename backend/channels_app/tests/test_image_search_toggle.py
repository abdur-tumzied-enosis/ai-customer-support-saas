from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import ChannelCredential, Widget
from knowledge.models import KnowledgeBase


class ImageSearchToggleTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="t@x.com", email="t@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-img-toggle")  # image search switches stay off
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-img-toggle")
		self.channel = ChannelCredential.objects.create(project=self.project, name="Chan", channel="whatsapp")
		self.widget = Widget.objects.create(project=self.project, name="Site", knowledge_base=self.kb)
		self.client = APIClient()
		self.client.force_authenticate(self.user)

	def test_channel_patch_toggles_image_search(self):
		url = f"/api/channels/channels/{self.channel.id}"
		r = self.client.patch(url, {"image_search_enabled": True}, format="json")
		assert r.status_code == 200, r.content
		assert r.json()["data"]["channel"]["image_search_enabled"] is True
		self.channel.refresh_from_db()
		assert self.channel.image_search_enabled is True
		r = self.client.patch(url, {"image_search_enabled": False}, format="json")
		assert r.json()["data"]["channel"]["image_search_enabled"] is False

	def test_widget_patch_toggles_image_search(self):
		url = f"/api/channels/widgets/{self.widget.id}"
		r = self.client.patch(url, {"image_search_enabled": True}, format="json")
		assert r.status_code == 200, r.content
		assert r.json()["data"]["widget"]["image_search_enabled"] is True
		self.widget.refresh_from_db()
		assert self.widget.image_search_enabled is True

	def test_defaults_off(self):
		assert ChannelCredential.objects.get(id=self.channel.id).image_search_enabled is False
		assert Widget.objects.get(id=self.widget.id).image_search_enabled is False
		r = self.client.get(f"/api/channels/channels/{self.channel.id}")
		assert r.json()["data"]["channel"]["image_search_enabled"] is False
