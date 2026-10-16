from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser


class ConversationCreateTest(TestCase):
	def test_malformed_knowledge_base_id_is_404(self):
		user = get_user_model().objects.create_user(username="u@x.com", email="u@x.com", password="pw-12345678")
		project = Project.objects.create(name="P", slug="p-conv")
		ProjectUser.objects.create(project=project, user=user, role="admin")
		client = APIClient()
		client.force_authenticate(user=user)
		res = client.post("/api/channels/conversations", {"project_slug": "p-conv", "knowledge_base_id": "not-a-uuid"}, format="json")
		assert res.status_code == 404
