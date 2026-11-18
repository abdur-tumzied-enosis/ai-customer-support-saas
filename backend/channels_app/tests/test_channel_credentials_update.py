from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import ChannelCredential


class ChannelCredentialMergeTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="c@x.com", email="c@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-cred")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=user)
		self.channel = ChannelCredential.objects.create(
			project=self.project, channel="facebook", name="FB",
			credentials={"page_id": "123", "page_access_token": "tok", "meta_app_id": "app1",
				"meta_app_secret": "sec", "verify_token": "vt"},
		)

	def _patch(self, credentials):
		return self.client.patch(f"/api/channels/channels/{self.channel.id}", {"credentials": credentials}, format="json")

	def test_updating_one_field_keeps_the_others(self):
		res = self._patch({"verify_token": "new-vt"})
		self.assertEqual(res.status_code, 200)
		self.channel.refresh_from_db()
		self.assertEqual(self.channel.credentials, {
			"page_id": "123", "page_access_token": "tok", "meta_app_id": "app1",
			"meta_app_secret": "sec", "verify_token": "new-vt",
		})

	def test_empty_values_are_not_stored_over_existing_ones(self):
		self._patch({"meta_app_secret": "   ", "page_access_token": "tok2"})
		self.channel.refresh_from_db()
		self.assertEqual(self.channel.credentials["meta_app_secret"], "sec")
		self.assertEqual(self.channel.credentials["page_access_token"], "tok2")


class ChannelCreateWithoutCredentialsTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="n@x.com", email="n@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-new")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=user)

	def test_name_only_creates_channel_with_verify_token(self):
		res = self.client.post("/api/channels/channels", {
			"project_slug": "p-new", "name": "Main FB", "channel": "facebook", "credentials": {"page_id": "  "},
		}, format="json")
		self.assertEqual(res.status_code, 201)
		cred = ChannelCredential.objects.get(project=self.project, name="Main FB")
		self.assertTrue(cred.credentials["verify_token"])
		self.assertNotIn("page_id", cred.credentials)
