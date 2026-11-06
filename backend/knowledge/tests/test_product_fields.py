from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductField

User = get_user_model()


class ProductFieldTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-fields")
		self.pu = ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def make_field(self, **extra):
		body = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "key": "size", "label": "Size"}
		body.update(extra)
		return self.client.post("/api/kb/product-fields/", body, format="json")

	def make_product(self, name="Shoe", **extra):
		extra.setdefault("price", "100")
		extra.setdefault("status", "active")
		return Product.objects.create(kb=self.kb, name=name, **extra)

	def test_required_field_makes_product_needs_info(self):
		self.make_field(required=True)
		r = self.client.post("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id),
			"name": "Shoe", "price": "100"}, format="json")
		self.assertEqual(r.json()["data"]["product"]["status"], "needs_info")
		r = self.client.post("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id),
			"name": "Boot", "price": "100", "custom": {"size": "42"}}, format="json")
		self.assertEqual(r.json()["data"]["product"]["status"], "active")

	def test_adding_required_field_recomputes_status(self):
		has = self.make_product("Has", custom={"size": "1"})
		lacks = self.make_product("Lacks")
		hidden = self.make_product("Hidden", status="hidden")
		r = self.make_field(required=True)
		self.assertEqual(r.status_code, 201, r.content)
		fid = r.json()["data"]["id"]
		has.refresh_from_db(); lacks.refresh_from_db(); hidden.refresh_from_db()
		self.assertEqual(has.status, "active")
		self.assertEqual(lacks.status, "needs_info")
		self.assertEqual(hidden.status, "hidden")
		r = self.client.patch(f"/api/kb/product-fields/{fid}/", {"required": False}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		lacks.refresh_from_db()
		self.assertEqual(lacks.status, "active")
		self.client.patch(f"/api/kb/product-fields/{fid}/", {"required": True}, format="json")
		lacks.refresh_from_db()
		self.assertEqual(lacks.status, "needs_info")
		self.client.delete(f"/api/kb/product-fields/{fid}/")
		lacks.refresh_from_db()
		self.assertEqual(lacks.status, "active")

	def test_key_unique_per_kb(self):
		self.assertEqual(self.make_field().status_code, 201)
		r = self.make_field()
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "duplicate_key")

	def test_choice_requires_choices(self):
		for extra in ({"kind": "choice"}, {"kind": "choice", "choices": []}):
			r = self.make_field(**extra)
			self.assertEqual(r.status_code, 400)
			self.assertEqual(r.json()["error"]["code"], "choices_required")
		r = self.make_field(kind="choice", choices=["S", "M"])
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(r.json()["data"]["choices"], ["S", "M"])

	def test_delete_keeps_custom_values(self):
		self.make_field(required=True)
		p = self.make_product(custom={"size": "42"})
		fid = ProductField.objects.get(kb=self.kb, key="size").id
		r = self.client.delete(f"/api/kb/product-fields/{fid}/")
		self.assertEqual(r.status_code, 200, r.content)
		p.refresh_from_db()
		self.assertEqual(p.custom, {"size": "42"})
		self.assertFalse(ProductField.objects.filter(id=fid).exists())

	def test_member_can_list_but_not_manage(self):
		self.make_field()
		self.pu.role = "member"
		self.pu.save()
		r = self.client.get("/api/kb/product-fields/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 200)
		self.assertEqual(len(r.json()["data"]), 1)
		self.assertEqual(self.make_field(key="other").status_code, 403)
		fid = ProductField.objects.get().id
		self.assertEqual(self.client.patch(f"/api/kb/product-fields/{fid}/", {"label": "x"}, format="json").status_code, 403)
		self.assertEqual(self.client.delete(f"/api/kb/product-fields/{fid}/").status_code, 403)

	def test_invalid_keys_rejected(self):
		for key in ("Size", "has space", "a" * 65, "1abc", "a.b", "\u00e9a"):
			r = self.make_field(key=key)
			self.assertEqual(r.status_code, 400, key)
			self.assertEqual(r.json()["error"]["code"], "invalid_value", key)
		self.assertEqual(ProductField.objects.filter(kb=self.kb).count(), 0)

	def test_valid_key_boundaries(self):
		self.assertEqual(self.make_field(key="a" * 64).status_code, 201)
		self.assertEqual(self.make_field(key="size_2").status_code, 201)
		self.assertTrue(ProductField.objects.filter(kb=self.kb, key="a" * 64).exists())

	def test_recompute_integrity_error_not_duplicate_key(self):
		# the server error is handled (not re-raised) by the stack, so assert a 500 that is not the duplicate_key envelope
		with mock.patch("knowledge.catalog_views.recompute_kb_status", side_effect=IntegrityError("boom")):
			r = self.make_field()
		self.assertEqual(r.status_code, 500)
		self.assertNotIn("duplicate_key", r.content.decode())
