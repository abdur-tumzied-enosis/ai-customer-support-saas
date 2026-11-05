from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product

User = get_user_model()


@override_settings(IMAGE_SEARCH_ENABLED=False)
class CatalogApiTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-cat")
		self.pu = ProjectUser.objects.create(project=self.project, user=self.user, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def create(self, **extra):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "name": "Shoe"}
		data.update(extra)
		return self.client.post("/api/kb/products/", data, format="json")

	def patch(self, pid, **body):
		return self.client.patch(f"/api/kb/products/{pid}/", body, format="json")

	def test_catalog_crud_works_with_image_search_off(self):
		r = self.create(sku="S1")
		self.assertEqual(r.status_code, 200, r.content)
		product = r.json()["data"]["product"]
		self.assertEqual(product["status"], "needs_info")
		self.assertEqual(product["currency"], "BDT")
		self.assertFalse(r.json()["data"]["key_warning"])
		listed = self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(listed.status_code, 200)
		self.assertEqual(len(listed.json()["data"]), 1)

	def test_patch_price_makes_active_and_locks_price(self):
		pid = self.create().json()["data"]["product"]["id"]
		r = self.patch(pid, price="250")
		self.assertEqual(r.status_code, 200, r.content)
		data = r.json()["data"]
		self.assertEqual(data["status"], "active")
		self.assertEqual(data["price"], "250.00")
		self.assertIn("price", data["locked_fields"])

	def test_patch_active_without_price_rejected(self):
		pid = self.create().json()["data"]["product"]["id"]
		r = self.patch(pid, status="active")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "price_required")
		self.assertEqual(Product.objects.get(id=pid).status, "needs_info")

	def test_photo_endpoints_still_gated(self):
		pid = self.create().json()["data"]["product"]["id"]
		self.pu.role = "admin"
		self.pu.save()
		r = self.client.post(f"/api/kb/products/{pid}/images/", {"files": []}, format="json")
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.create(files=[{"name": "a.jpg", "size": 10, "content_type": "image/jpeg"}])
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		self.assertEqual(Product.objects.count(), 1)

	def test_summary_counts(self):
		a = self.create(price="10").json()["data"]["product"]["id"]
		self.create(name="B")
		c = self.create(name="C", price="5").json()["data"]["product"]["id"]
		self.assertEqual(self.patch(c, status="hidden").status_code, 200)
		r = self.client.get("/api/kb/products/summary/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"], {"counts": {"needs_info": 1, "active": 1, "hidden": 1}, "currency": "BDT"})
		self.assertEqual(Product.objects.get(id=a).status, "active")

	def test_key_warning_when_no_key(self):
		self.assertTrue(self.create().json()["data"]["key_warning"])
		self.assertFalse(self.create(name="X", external_id="E1").json()["data"]["key_warning"])

	def test_member_files_403_member_without_files_ok(self):
		r = self.create(files=[{"name": "a.jpg", "size": 10, "content_type": "image/jpeg"}])
		self.assertEqual(r.status_code, 403)
		self.assertEqual(r.json()["error"]["code"], "access_denied")
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(self.create().status_code, 200)  # no photos: created and returned as 200

	def test_member_can_patch(self):
		pid = self.create().json()["data"]["product"]["id"]
		self.assertEqual(self.patch(pid, price="5").status_code, 200)

	def test_non_member_denied_on_list(self):
		other = User.objects.create_user(username="o@example.com", email="o@example.com", password="x")
		self.client.force_authenticate(user=other)
		r = self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 403)  # existing behaviour: non-member -> access_denied

	def test_delete_admin_only_with_image_search_off(self):
		pid = self.create().json()["data"]["product"]["id"]
		self.assertEqual(self.client.delete(f"/api/kb/products/{pid}/").status_code, 403)
		self.pu.role = "admin"
		self.pu.save()
		self.assertEqual(self.client.delete(f"/api/kb/products/{pid}/").status_code, 200)

	def test_invalid_price_and_overflow(self):
		for bad in ("abc", "9999999999.996", "-1"):
			r = self.create(price=bad)
			self.assertEqual(r.status_code, 400, bad)
			self.assertEqual(r.json()["error"]["code"], "invalid_price", bad)

	def test_duplicate_external_id(self):
		self.assertEqual(self.create(external_id="E1").status_code, 200)
		r = self.create(name="Other", external_id="E1")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "duplicate_external_id")

	def test_duplicate_external_id_race(self):
		self.create(external_id="E1")
		real = Product.objects.filter
		def fake(*a, **kw):
			if "external_id" in kw:
				return Product.objects.none()
			return real(*a, **kw)
		with patch.object(Product.objects, "filter", side_effect=fake):
			r = self.create(name="Other", external_id="E1")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "duplicate_external_id")

	def test_invalid_status(self):
		pid = self.create().json()["data"]["product"]["id"]
		r = self.patch(pid, status="bogus")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_status")

	def test_unhide_computes_status(self):
		pid = self.create(price="10").json()["data"]["product"]["id"]
		self.assertEqual(self.patch(pid, status="hidden").json()["data"]["status"], "hidden")
		r = self.patch(pid, status="needs_info")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["status"], "active")
		self.assertEqual(Product.objects.get(id=pid).status, "active")

	def test_non_string_name_and_non_dict_body(self):
		for body in ({"name": 123}, {"name": ["x"]}):
			r = self.create(**body)
			self.assertEqual(r.status_code, 400, body)
			self.assertEqual(r.json()["error"]["code"], "invalid_value")
		pid = self.create().json()["data"]["product"]["id"]
		r = self.patch(pid, name=123)
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_value")
		r = self.client.post("/api/kb/products/", [1, 2], format="json")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_value")
		r = self.client.patch(f"/api/kb/products/{pid}/", [1, 2], format="json")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_value")

	def test_list_query_count_bounded(self):
		def count():
			with CaptureQueriesContext(connection) as ctx:
				r = self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
			self.assertEqual(r.status_code, 200)
			return len(ctx)
		for i in range(3):
			self.create(name=f"P{i}")
		three = count()
		self.create(name="P3")
		self.assertEqual(count(), three)
