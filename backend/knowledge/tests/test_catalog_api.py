from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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
