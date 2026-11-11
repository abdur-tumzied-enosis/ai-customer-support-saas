from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Widget
from channels_app.service.conversation_service import create_conversation
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from accounts.views import purge_project
from core.storage import thumb_key
from core.tests.utils import fresh_storage, stored_files
from knowledge.models import Document, KnowledgeBase, Product, ProductImage

User = get_user_model()


class AccountSettingsTests(TestCase):
	def setUp(self):
		cache.clear()  # auth throttle
		self.user = User.objects.create_user(username="a@x.com", email="a@x.com", password="old-pass-123", first_name="A")
		self.solo = Project.objects.create(name="Solo", slug="solo")
		self.shared = Project.objects.create(name="Shared", slug="shared")
		ProjectUser.objects.create(project=self.solo, user=self.user)
		ProjectUser.objects.create(project=self.shared, user=self.user)
		self.teammate = User.objects.create_user(username="b@x.com", email="b@x.com", password="pw-123456")
		ProjectUser.objects.create(project=self.shared, user=self.teammate)
		self.kb = KnowledgeBase.objects.create(project=self.solo, name="kb", vector_collection="vc-solo")
		self.doc = Document.objects.create(kb=self.kb, file="documents/solo/x/a.txt", filename="a.txt")
		# Both reference the KB with on_delete=PROTECT
		Widget.objects.create(project=self.solo, knowledge_base=self.kb, name="w")
		create_conversation(self.solo, knowledge_base=self.kb, name="c")
		self.client = APIClient()
		self.client.force_login(self.user)

	def test_profile_update(self):
		r = self.client.patch("/api/auth/profile/", {"first_name": " Ada ", "last_name": "Lovelace"}, format="json")
		assert r.status_code == 200, r.content
		self.user.refresh_from_db()
		assert (self.user.first_name, self.user.last_name) == ("Ada", "Lovelace")
		assert self.client.patch("/api/auth/profile/", {"first_name": ""}, format="json").status_code == 400

	def test_change_password(self):
		bad = self.client.post("/api/auth/password/", {"current_password": "nope", "new_password": "new-pass-456"}, format="json")
		assert bad.status_code == 400
		weak = self.client.post("/api/auth/password/", {"current_password": "old-pass-123", "new_password": "123"}, format="json")
		assert weak.status_code == 400
		ok = self.client.post("/api/auth/password/", {"current_password": "old-pass-123", "new_password": "new-pass-456"}, format="json")
		assert ok.status_code == 200, ok.content
		self.user.refresh_from_db()
		assert self.user.check_password("new-pass-456")
		assert self.client.get("/api/auth/me/").status_code == 200  # this session survives the change

	def test_export_has_projects_and_no_secrets(self):
		r = self.client.get("/api/auth/export/")
		assert r.status_code == 200
		assert "attachment" in r["Content-Disposition"]
		data = r.json()
		assert data["user"]["email"] == "a@x.com" and "password" not in data["user"]
		solo = next(p for p in data["projects"] if p["name"] == "Solo")
		assert solo["documents"][0]["fields"]["filename"] == "a.txt"
		assert "pbkdf2" not in r.content.decode()

	@mock.patch("accounts.views.default_storage.delete")
	@mock.patch("agents.vector_store.delete_collection")
	def test_delete_account(self, delete_collection, delete_file):
		assert self.client.post("/api/auth/delete/", {"password": "wrong"}, format="json").status_code == 400
		assert User.objects.filter(pk=self.user.pk).exists()

		r = self.client.post("/api/auth/delete/", {"password": "old-pass-123"}, format="json")
		assert r.status_code == 200, r.content
		assert not User.objects.filter(pk=self.user.pk).exists()
		assert not Project.objects.filter(pk=self.solo.pk).exists()
		assert not Document.objects.filter(pk=self.doc.pk).exists()
		assert Project.objects.filter(pk=self.shared.pk).exists()  # teammate keeps the shared project
		delete_collection.assert_any_call("vc-solo")
		delete_collection.assert_any_call("vc-solo_web")
		delete_collection.assert_any_call("vc-solo_images")  # legacy photo collection
		delete_file.assert_called_once_with("documents/solo/x/a.txt")
		assert self.client.get("/api/auth/me/").status_code in (401, 403)

	@mock.patch("agents.vector_store.delete_collection", side_effect=ConnectionError("qdrant down"))
	def test_delete_aborts_before_rows_when_vectors_cannot_be_removed(self, _):
		r = self.client.post("/api/auth/delete/", {"password": "old-pass-123"}, format="json")
		assert r.status_code == 500  # core.middleware.ExceptionHandlingMiddleware; the user can retry
		assert User.objects.filter(pk=self.user.pk).exists()
		assert Project.objects.filter(pk=self.solo.pk).exists()


@fresh_storage
class PurgeStorageTests(TestCase):
	@mock.patch("agents.vector_store.delete_collection")
	@mock.patch("knowledge.models.delete_images")
	def test_purge_removes_photo_variants_and_customer_media(self, _delete_images, _delete_collection):
		project = Project.objects.create(name="Solo", slug="solo-purge")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-solo-purge")
		product = Product.objects.create(kb=kb, name="Shoe")
		img = ProductImage.objects.create(product=product, status="ready", file="products/solo-purge/x/i.webp")
		keep = default_storage.save("chat_media/other-project/b.webp", ContentFile(b"b"))
		for name in (img.file.name, thumb_key(img.file.name), "chat_media/solo-purge/a.webp", "chat_media/solo-purge/v.ogg"):
			default_storage.save(name, ContentFile(b"x"))
		purge_project(project)
		assert stored_files() == [keep]
