from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.credential_crypto import encrypt_credentials
from channels_app.meta_status import GraphError
from channels_app.models import ChannelCredential
from knowledge.models import KnowledgeBase

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PAGES = [
	{"id": "p1", "name": "Shop", "access_token": "tok-p1",
		"instagram_account_id": "ig1", "instagram_username": "shop"},
	{"id": "p2", "name": "Cafe", "access_token": "tok-p2",
		"instagram_account_id": "", "instagram_username": ""},
]


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY, META_APP_ID="1478910681051836", CACHES=LOCMEM_CACHE)
class MetaConnectApiTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="m@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-meta")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default",
			is_default=True, vector_collection="vc-meta")
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def token(self, pages=None):
		with mock.patch("channels_app.meta_connect.exchange_long_lived_token", return_value="long"), \
			mock.patch("channels_app.meta_connect.get_meta_user_id", return_value="mu1"), \
			mock.patch("channels_app.meta_connect.list_manageable_pages", return_value=PAGES if pages is None else pages), \
			mock.patch("channels_app.meta_connect.subscribe_page_for_oauth") as sub:
			res = self.client.post("/api/channels/meta/token",
				{"project_slug": "p-meta", "access_token": "short"}, format="json")
		return res, sub

	def connect(self, setup_id, page_id="p1"):
		with mock.patch("channels_app.meta_connect.subscribe_page_for_oauth"):
			return self.client.post("/api/channels/meta/connect",
				{"project_slug": "p-meta", "setup_id": setup_id, "page_id": page_id},
				format="json")

	def test_one_page_connects_immediately_with_both_rows(self):
		res, sub = self.token([PAGES[0]])
		assert res.status_code == 200 and res.data["data"]["connected"] is True
		assert sorted(c["channel"] for c in res.data["data"]["channels"]) == ["facebook", "instagram"]
		assert sub.call_count == 1
		row = ChannelCredential.objects.get(channel="instagram", project=self.project)
		assert row.knowledge_base_id == self.kb.id
		assert row.credentials["connected_via"] == "oauth"
		assert row.credentials["meta_user_id"] == "mu1"
		assert row.credentials["instagram_account_id"] == "ig1"
		assert row.credentials["page_access_token"].startswith("enc:v1:")

	def test_page_without_instagram_connects_messenger_only(self):
		res, _ = self.token([PAGES[1]])
		assert res.status_code == 200 and res.data["data"]["connected"] is True
		assert [c["channel"] for c in res.data["data"]["channels"]] == ["facebook"]
		assert not ChannelCredential.objects.filter(channel="instagram").exists()

	def test_no_eligible_pages_returns_an_error(self):
		res, sub = self.token([])
		assert res.status_code == 400 and res.data["error"]["code"] == "no_pages"
		sub.assert_not_called()

	def test_multiple_pages_return_a_picker(self):
		res, sub = self.token()
		assert res.status_code == 200 and res.data["data"]["connected"] is False
		assert res.data["data"]["setup_id"]
		assert [p["instagram_username"] for p in res.data["data"]["pages"]] == ["shop", ""]
		sub.assert_not_called()

	def test_picker_connect_creates_both_rows(self):
		res, _ = self.token()
		setup_id = res.data["data"]["setup_id"]
		res = self.connect(setup_id, "p1")
		assert res.status_code == 200 and res.data["data"]["connected"] is True
		assert ChannelCredential.objects.filter(project=self.project).count() == 2

	def test_same_page_reconnect_updates_in_place(self):
		self.token([PAGES[0]])
		assert ChannelCredential.objects.filter(project=self.project).count() == 2
		self.token([PAGES[0]])
		assert ChannelCredential.objects.filter(project=self.project).count() == 2

	def test_other_project_page_returns_conflict(self):
		other = Project.objects.create(name="Other", slug="p-other")
		ChannelCredential.objects.create(project=other, channel="facebook", is_active=True,
			credentials={"page_id": "p1", "connected_via": "oauth"})
		res, sub = self.token([PAGES[0]])
		assert res.status_code == 409 and res.data["error"]["code"] == "page_connected_elsewhere"
		sub.assert_not_called()
		assert not ChannelCredential.objects.filter(project=self.project).exists()

	def test_subscribe_failure_on_new_connect_writes_nothing(self):
		with mock.patch("channels_app.meta_connect.exchange_long_lived_token", return_value="long"), \
			mock.patch("channels_app.meta_connect.get_meta_user_id", return_value="mu1"), \
			mock.patch("channels_app.meta_connect.list_manageable_pages", return_value=[PAGES[0]]), \
			mock.patch("channels_app.meta_connect.subscribe_page_for_oauth", side_effect=GraphError("nope")):
			res = self.client.post("/api/channels/meta/token",
				{"project_slug": "p-meta", "access_token": "short"}, format="json")
		assert res.status_code == 502 and res.data["error"]["code"] == "meta_error"
		assert not ChannelCredential.objects.filter(project=self.project).exists()

	def test_subscribe_failure_on_reconnect_keeps_old_credential(self):
		self.token([PAGES[0]])
		old = ChannelCredential.objects.get(channel="facebook", project=self.project)
		old_token = encrypt_credentials({"page_access_token": "old"})["page_access_token"]
		old.credentials["page_access_token"] = old_token
		old.save(update_fields=["credentials"])
		with mock.patch("channels_app.meta_connect.exchange_long_lived_token", return_value="long"), \
			mock.patch("channels_app.meta_connect.get_meta_user_id", return_value="mu1"), \
			mock.patch("channels_app.meta_connect.list_manageable_pages", return_value=[PAGES[0]]), \
			mock.patch("channels_app.meta_connect.subscribe_page_for_oauth", side_effect=GraphError("nope")):
			res = self.client.post("/api/channels/meta/token",
				{"project_slug": "p-meta", "access_token": "short"}, format="json")
		assert res.status_code == 502
		old.refresh_from_db()
		assert old.is_active and old.credentials["page_access_token"] == old_token

	def test_missing_default_kb_blocks_before_token_exchange(self):
		self.kb.is_default = False
		self.kb.save(update_fields=["is_default"])
		with mock.patch("channels_app.meta_connect.exchange_long_lived_token") as exchange:
			res = self.client.post("/api/channels/meta/token",
				{"project_slug": "p-meta", "access_token": "short"}, format="json")
		assert res.status_code == 400 and res.data["error"]["code"] == "no_default_knowledge_base"
		exchange.assert_not_called()

	def test_page_not_in_session_is_rejected(self):
		res, _ = self.token()  # two pages -> picker session, not an immediate connect
		res = self.connect(res.data["data"]["setup_id"], "p-other")
		assert res.status_code == 400 and res.data["error"]["code"] == "invalid_page"

	def test_failed_connect_keeps_the_session_for_retry(self):
		res, _ = self.token()
		setup_id = res.data["data"]["setup_id"]
		with mock.patch("channels_app.meta_connect.subscribe_page_for_oauth", side_effect=GraphError("nope")):
			res = self.client.post("/api/channels/meta/connect",
				{"project_slug": "p-meta", "setup_id": setup_id, "page_id": "p1"}, format="json")
		assert res.status_code == 502
		assert self.connect(setup_id, "p1").status_code == 200  # same setup_id still works

	def test_setup_session_cannot_be_reused(self):
		res, _ = self.token()
		setup_id = res.data["data"]["setup_id"]
		assert self.connect(setup_id, "p1").status_code == 200
		assert self.connect(setup_id, "p1").status_code == 400

	def test_setup_session_rejects_a_different_user(self):
		res, _ = self.token()
		setup_id = res.data["data"]["setup_id"]
		other_user = get_user_model().objects.create_user(username="o@x.com", password="pw-12345678")
		ProjectUser.objects.create(project=self.project, user=other_user, role="admin")
		self.client.force_authenticate(user=other_user)
		assert self.connect(setup_id, "p1").status_code == 400

	def test_setup_session_rejects_a_different_project(self):
		res, _ = self.token()
		setup_id = res.data["data"]["setup_id"]
		ProjectUser.objects.create(project=Project.objects.create(name="Second", slug="p-second"),
			user=self.user, role="admin")
		with mock.patch("channels_app.meta_connect.subscribe_page_for_oauth"):
			res = self.client.post("/api/channels/meta/connect",
				{"project_slug": "p-second", "setup_id": setup_id, "page_id": "p1"}, format="json")
		assert res.status_code == 400 and res.data["error"]["code"] == "invalid_setup"

	def test_graph_error_from_accounts_is_a_502(self):
		with mock.patch("channels_app.meta_connect.exchange_long_lived_token", return_value="long"), \
			mock.patch("channels_app.meta_connect.get_meta_user_id", return_value="mu1"), \
			mock.patch("channels_app.meta_connect.list_manageable_pages", side_effect=GraphError("graph down")):
			res = self.client.post("/api/channels/meta/token",
				{"project_slug": "p-meta", "access_token": "short"}, format="json")
		assert res.status_code == 502 and res.data["error"]["code"] == "meta_error"
