from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.catalog import compute_status, effective_price, save_variants
from knowledge.models import KnowledgeBase, Product

User = get_user_model()


@override_settings(IMAGE_SEARCH_ENABLED=False)
class VariantTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-var")
		ProjectUser.objects.create(project=self.project, user=self.user, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def product(self, price="100"):
		return Product.objects.create(kb=self.kb, name="Shoe", price=None if price is None else Decimal(price), status="active" if price else "needs_info")

	def create(self, **extra):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "name": "Shoe"}
		data.update(extra)
		return self.client.post("/api/kb/products/", data, format="json")

	def patch(self, pid, **body):
		return self.client.patch(f"/api/kb/products/{pid}/", body, format="json")

	def test_variant_without_price_uses_product_price(self):
		p = self.product("100")
		save_variants(p, [{"label": "L"}])
		p.refresh_from_db()
		v = p.variants.get()
		self.assertEqual(effective_price(p, v), Decimal("100.00"))
		self.assertEqual(p.status, "active")

	def test_available_variant_without_any_price_needs_info(self):
		p = self.product(None)
		save_variants(p, [{"label": "L"}])
		p.refresh_from_db()
		self.assertEqual(p.status, "needs_info")

	def test_unavailable_variant_ignored_for_status(self):
		p = self.product("100")
		save_variants(p, [{"label": "L", "available": False}])
		self.assertEqual(compute_status(p, []), "active")

	def test_save_replaces_set(self):
		p = self.product()
		save_variants(p, [{"label": "A"}, {"label": "B"}])
		save_variants(p, [{"label": "C"}])
		self.assertEqual([v.label for v in p.variants.all()], ["C"])

	def test_post_with_variants(self):
		r = self.create(price="10", variants=[{"label": "B", "price": "12.5", "attributes": {"size": "L"}, "external_id": "x1"}, {"label": "A"}])
		self.assertEqual(r.status_code, 200, r.content)
		vs = r.json()["data"]["product"]["variants"]
		self.assertEqual([v["label"] for v in vs], ["A", "B"])
		self.assertEqual(vs[1]["price"], "12.50")
		self.assertEqual(vs[1]["attributes"], {"size": "L"})
		self.assertTrue(vs[0]["available"])

	def test_patch_variants_recomputes_status(self):
		pid = self.create().json()["data"]["product"]["id"]
		r = self.patch(pid, price="10", variants=[{"label": "A"}])
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["status"], "active")
		self.assertEqual(len(r.json()["data"]["variants"]), 1)
		r = self.patch(pid, variants=[])
		self.assertEqual(r.json()["data"]["variants"], [])

	def test_patch_without_variants_keeps_them(self):
		pid = self.create(price="10", variants=[{"label": "A"}]).json()["data"]["product"]["id"]
		r = self.patch(pid, description="x")
		self.assertEqual(len(r.json()["data"]["variants"]), 1)

	def test_patch_hidden_stays_hidden_with_variants(self):
		pid = self.create(price="10").json()["data"]["product"]["id"]
		r = self.patch(pid, status="hidden", variants=[{"label": "A"}])
		self.assertEqual(r.json()["data"]["status"], "hidden")

	def test_duplicate_external_id_rejected(self):
		r = self.create(price="10", variants=[{"label": "A", "external_id": "k"}, {"label": "B", "external_id": "k"}])
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "duplicate_variant_key")
		self.assertFalse(Product.objects.exists())

	def test_invalid_variants_rejected_without_change(self):
		pid = self.create(price="10", variants=[{"label": "A"}]).json()["data"]["product"]["id"]
		bad = [
			"x", [1], [{}], [{"label": "  "}], [{"label": 5}], [{"label": "a" * 256}],
			[{"label": "A", "attributes": []}], [{"label": "A", "attributes": {"k": 1}}],
			[{"label": "A", "price": "-1"}], [{"label": "A", "price": "abc"}], [{"label": "A", "price": "1e12"}],
			[{"label": "A", "price": True}], [{"label": "A", "available": "yes"}],
			[{"label": "A", "external_id": ["x"]}], [{"label": "A", "sku": {"a": 1}}],
		]
		for variants in bad:
			r = self.patch(pid, variants=variants)
			self.assertEqual(r.status_code, 400, (variants, r.content))
			self.assertIn(r.json()["error"]["code"], ("invalid_value", "invalid_price"))
		self.assertEqual(Product.objects.get(id=pid).variants.count(), 1)

	def test_list_has_no_per_product_variant_queries(self):
		for i in range(3):
			self.create(price="10", name=f"P{i}", variants=[{"label": "A"}])
		with self.assertNumQueries(6):
			r = self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(len(r.json()["data"][0]["variants"]), 1)
