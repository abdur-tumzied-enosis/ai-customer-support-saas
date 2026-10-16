from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase

URL = "/api/kb/knowledge-bases/"


class KnowledgeBaseApiTest(TestCase):
	def setUp(self):
		User = get_user_model()
		self.admin = User.objects.create_user(username="admin@x.com", email="admin@x.com", password="pw-12345678")
		self.member = User.objects.create_user(username="member@x.com", email="member@x.com", password="pw-12345678")
		self.outsider = User.objects.create_user(username="out@x.com", email="out@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-kbapi")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.client = APIClient()

	def as_user(self, user):
		self.client.force_authenticate(user=user)
		return self.client

	def create(self, name, user=None):
		return self.as_user(user or self.admin).post(URL, {"project_id": str(self.project.id), "name": name}, format="json")

	def rename(self, kb, name, user=None):
		return self.as_user(user or self.admin).patch(f"{URL}{kb.id}/", {"name": name}, format="json")

	def test_list_puts_default_first_with_top_level_document_counts(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="Alpha")
		parent = Document.objects.create(kb=kb, source_type="url")
		Document.objects.create(kb=kb, source_type="url", parent_crawl_id=parent)  # crawled child page: not counted
		res = self.as_user(self.member).get(URL, {"project_id": str(self.project.id)})
		assert res.status_code == 200
		rows = res.data["data"]["knowledge_bases"]
		assert [(r["name"], r["is_default"], r["document_count"]) for r in rows] == [("default", True, 0), ("Alpha", False, 1)]

	def test_list_denies_outsider_and_malformed_project(self):
		assert self.as_user(self.outsider).get(URL, {"project_id": str(self.project.id)}).status_code == 403
		assert self.as_user(self.member).get(URL, {"project_id": "not-a-uuid"}).status_code == 403
		assert self.as_user(self.member).get(URL).status_code == 400

	def test_create_trims_name_and_names_collection_by_id(self):
		res = self.create("  Products ")
		assert res.status_code == 201
		kb = KnowledgeBase.objects.get(id=res.data["data"]["knowledge_base"]["id"])
		assert kb.name == "Products" and not kb.is_default
		assert kb.vector_collection == f"kb_p-kbapi_{kb.id.hex}"
		assert KnowledgeBase.objects.filter(project=self.project, is_default=True).exists()

	def test_create_rejects_duplicate_ignoring_case(self):
		assert self.create("Products").status_code == 201
		res = self.create("products")
		assert res.status_code == 409 and res.data["error"]["code"] == "duplicate_name"

	def test_create_rejects_blank_and_long_names(self):
		assert self.create("   ").status_code == 400
		assert self.create("x" * 101).status_code == 400

	def test_create_requires_admin(self):
		assert self.create("Products", user=self.member).status_code == 403
		assert self.create("Products", user=self.outsider).status_code == 403

	def test_rename_default_keeps_it_default(self):
		kb = KnowledgeBase.get_default(self.project)
		res = self.rename(kb, "Main")
		assert res.status_code == 200 and res.data["data"]["knowledge_base"]["name"] == "Main"
		assert KnowledgeBase.get_default(self.project).id == kb.id

	def test_rename_to_own_name_in_other_case_is_allowed(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="Products")
		assert self.rename(kb, "products").status_code == 200

	def test_rename_rejects_duplicate_non_admin_and_unknown(self):
		KnowledgeBase.objects.create(project=self.project, name="Products")
		kb = KnowledgeBase.objects.create(project=self.project, name="FAQ")
		assert self.rename(kb, "PRODUCTS").status_code == 409
		assert self.rename(kb, "New", user=self.member).status_code == 403
		res = self.as_user(self.admin).patch(f"{URL}00000000-0000-0000-0000-000000000000/", {"name": "x"}, format="json")
		assert res.status_code == 404
