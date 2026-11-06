from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, IgnoredProductPage, KnowledgeBase, Product, ProductCandidate

User = get_user_model()
URL = "/api/kb/products/candidates/"


class CandidatesApiTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-cand")
		self.pu = ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.base = {"project_id": str(self.project.id), "kb_id": str(self.kb.id)}

	def cand(self, name="Shirt", page="https://shop.test/p1", confidence=0.9, **payload):
		payload.setdefault("price", "500")
		return ProductCandidate.objects.create(kb=self.kb, page_url=page, name=name, payload=payload, confidence=confidence)

	def do_import(self, *cands):
		with self.captureOnCommitCallbacks(execute=True):
			r = self.client.post(URL + "import/", {**self.base, "ids": [str(c.id) for c in cands]}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		return r.json()["data"]

	def test_list_groups_by_page_and_flags_low_confidence(self):
		Document.objects.create(kb=self.kb, source_type="url", page_url="https://shop.test/p1", page_title="Shop")
		self.cand("A", confidence=0.5)
		self.cand("B", confidence=0.6)
		r = self.client.get(URL, self.base)
		data = r.json()["data"]
		self.assertEqual(len(data["pages"]), 1)
		self.assertEqual(data["pages"][0]["page_url"], "https://shop.test/p1")
		flags = {c["name"]: c["low_confidence"] for c in data["pages"][0]["candidates"]}
		self.assertEqual(flags, {"A": True, "B": False})

	def test_import_only_picked_candidates(self):
		a, b = self.cand("A"), self.cand("B")
		out = self.do_import(a)
		self.assertEqual((out["imported"], out["updated"], out["rejected"]), (1, 0, []))
		self.assertEqual(list(Product.objects.values_list("name", flat=True)), ["A"])
		b.refresh_from_db()
		self.assertEqual(b.status, "pending")

	def test_import_without_price_is_needs_info(self):
		self.do_import(self.cand("Free", price="call us"))
		p = Product.objects.get()
		self.assertIsNone(p.price)
		self.assertEqual(p.status, "needs_info")

	def test_candidate_import_without_price_is_needs_info(self):
		self.do_import(self.cand("NoPrice", price=None))
		self.assertEqual(Product.objects.get().status, "needs_info")

	def test_import_saves_page_url_and_price(self):
		self.do_import(self.cand("Shirt", sku="S1"))
		p = Product.objects.get()
		self.assertEqual((p.product_url, p.source, p.price, p.status, p.sku), ("https://shop.test/p1", "kb", Decimal("500"), "active", "S1"))

	def test_image_fetch_enqueued_on_commit_only(self):
		c = self.cand("Shirt", image_srcs=["https://shop.test/a.jpg"])
		with patch("knowledge.tasks.fetch_product_images.delay") as delay:
			self.do_import(c)
		delay.assert_called_once_with(str(Product.objects.get().id), ["https://shop.test/a.jpg"])

	def test_ignore_page_hides_candidates_and_remembers(self):
		self.cand("A")
		self.cand("B", page="https://shop.test/p2")
		r = self.client.post(URL + "ignore-page/", {**self.base, "page_url": "https://shop.test/p1"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertTrue(IgnoredProductPage.objects.filter(kb=self.kb, url="https://shop.test/p1").exists())
		data = self.client.get(URL, self.base).json()["data"]
		self.assertEqual([p["page_url"] for p in data["pages"]], ["https://shop.test/p2"])
		self.assertEqual(data["ignored_pages"], ["https://shop.test/p1"])

	def test_duplicate_external_id_row_rejected(self):
		a = self.cand("A", external_id="X1")
		b = self.cand("B", external_id="X1")
		c = self.cand("C", external_id="X3")
		out = self.do_import(a, b, c)
		self.assertEqual(out["imported"], 2)
		self.assertEqual([r["id"] for r in out["rejected"]], [str(b.id)])
		self.assertTrue(out["rejected"][0]["message"])
		self.assertEqual(sorted(Product.objects.values_list("name", flat=True)), ["A", "C"])

	def test_matched_candidate_updates_unlocked_and_keeps_locked(self):
		p = Product.objects.create(kb=self.kb, name="Shirt", sku="S1", price=Decimal("400"), description="old",
			status="active", locked_fields=["price"])
		c = self.cand("Shirt", sku="S1", price="999", description="new")
		out = self.do_import(c)
		self.assertEqual((out["imported"], out["updated"]), (0, 1))
		p.refresh_from_db()
		self.assertEqual(p.price, Decimal("400"))
		self.assertEqual(p.description, "new")
		self.assertEqual(p.locked_fields, ["price"])
		c.refresh_from_db()
		self.assertEqual(c.status, "imported")

	def test_matched_update_does_not_add_locked_fields(self):
		p = Product.objects.create(kb=self.kb, name="Shirt", sku="S1", price=Decimal("400"), status="active")
		self.do_import(self.cand("Shirt", sku="S1", price="450", description="d"))
		p.refresh_from_db()
		self.assertEqual(p.price, Decimal("450"))
		self.assertEqual(p.locked_fields, [])

	def test_list_returns_matched_by(self):
		self.cand("A", matched_by="name")
		c = self.client.get(URL, self.base).json()["data"]["pages"][0]["candidates"][0]
		self.assertEqual(c["matched_by"], "name")

	def test_matched_import_keeps_locked_status(self):
		p = Product.objects.create(kb=self.kb, name="Shirt", sku="S1", price=Decimal("400"), status="needs_info",
			locked_fields=["status"])
		self.do_import(self.cand("Shirt", sku="S1", price="450"))
		p.refresh_from_db()
		self.assertEqual((p.price, p.status), (Decimal("450"), "needs_info"))

	def test_two_candidates_for_one_product_create_one(self):
		out = self.do_import(self.cand("Shirt", price="500"), self.cand("Shirt", page="https://shop.test/p2", price="550"))
		self.assertEqual((out["imported"], out["updated"], out["rejected"]), (1, 1, []))
		self.assertEqual(Product.objects.count(), 1)
		self.assertEqual(Product.objects.get().price, Decimal("550"))

	def test_unknown_id_rejected_others_import(self):
		a = self.cand("A")
		with self.captureOnCommitCallbacks(execute=True):
			r = self.client.post(URL + "import/", {**self.base, "ids": ["nope", str(a.id)]}, format="json")
		data = r.json()["data"]
		self.assertEqual((data["imported"], len(data["rejected"])), (1, 1))

	def test_roles(self):
		self.pu.role = "member"
		self.pu.save()
		c = self.cand("A")
		self.assertEqual(self.client.get(URL, self.base).status_code, 200)
		self.assertEqual(self.client.post(URL + "import/", {**self.base, "ids": [str(c.id)]}, format="json").status_code, 403)
		self.assertEqual(self.client.post(URL + "ignore-page/", {**self.base, "page_url": "x"}, format="json").status_code, 403)
		self.assertEqual(self.client.post("/api/kb/products/extract/", self.base, format="json").status_code, 403)
		self.assertEqual(Product.objects.count(), 0)

	def test_extract_enqueues(self):
		with patch("knowledge.tasks.extract_product_candidates.delay") as delay:
			r = self.client.post("/api/kb/products/extract/", self.base, format="json")
		self.assertEqual(r.status_code, 202)
		self.assertEqual(r.json()["data"], {"queued": True})
		delay.assert_called_once_with(str(self.kb.id))
