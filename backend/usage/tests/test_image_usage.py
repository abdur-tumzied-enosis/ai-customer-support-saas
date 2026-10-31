import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from usage.models import UsageEvent
from usage.utils import record_image_usage

User = get_user_model()


class ImageSearchSwitchTests(TestCase):
	def test_image_search_on_needs_master_operator_and_tenant(self):
		p = Project.objects.create(name="P", slug="p", image_search_allowed=True)  # enabled defaults True
		with override_settings(IMAGE_SEARCH_ENABLED=True):
			self.assertTrue(p.image_search_on)
		with override_settings(IMAGE_SEARCH_ENABLED=False):
			self.assertFalse(p.image_search_on)
		p.image_search_enabled = False  # tenant off
		with override_settings(IMAGE_SEARCH_ENABLED=True):
			self.assertFalse(p.image_search_on)
			self.assertTrue(p.image_search_available)
		p.image_search_enabled, p.image_search_allowed = True, False  # operator off wins
		with override_settings(IMAGE_SEARCH_ENABLED=True):
			self.assertFalse(p.image_search_on)
			self.assertFalse(p.image_search_available)

	def test_defaults(self):
		p = Project.objects.create(name="P", slug="p")
		self.assertFalse(p.image_search_allowed)
		self.assertTrue(p.image_search_enabled)


class ImageUsageTests(TestCase):
	def setUp(self):
		cache.clear()
		self.project = Project.objects.create(name="P", slug="p")

	def test_record_image_usage(self):
		record_image_usage(str(self.project.id), "search", message_id="m1")
		e = UsageEvent.objects.get()
		self.assertEqual((e.kind, e.tokens, e.metadata), ("image", 0, {"op": "search", "message_id": "m1"}))


	def test_analytics_counts_photos_without_changing_tokens(self):
		user = User.objects.create_user(username="a@x.com", email="a@x.com", password="pw-123456")
		ProjectUser.objects.create(project=self.project, user=user)
		pid = str(self.project.id)
		for _ in range(2):
			record_image_usage(pid, "index")
		for _ in range(3):
			record_image_usage(pid, "search")
		UsageEvent.objects.create(project=self.project, kind="chat_in", tokens=100)
		client = APIClient()
		client.force_login(user)
		r = client.get("/api/analytics/overview/")
		self.assertEqual(r.status_code, 200, r.content)
		tu = r.json()["data"]["token_usage"]
		self.assertEqual((tu["photos_indexed"], tu["photo_searches"], tu["total"]), (2, 3, 100))


class ImageUsageNeverRaisesTests(TransactionTestCase):
	# Real commits: the FK check is deferred to commit, so TestCase would hide the failure until teardown.
	def test_record_image_usage_never_raises(self):
		record_image_usage(str(uuid.uuid4()), "index")  # FK violation
		self.assertEqual(UsageEvent.objects.count(), 0)
