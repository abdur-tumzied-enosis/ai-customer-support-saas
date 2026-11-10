from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from billing import service
from billing.models import ActionPrice, CreditTransaction
from knowledge.models import Document, KnowledgeBase, ProductCandidate
from knowledge.product_extract import ExtractedProduct, ExtractionResult, extract_candidates

User = get_user_model()


def page(n, tag="a"):
	return ExtractionResult(products=[ExtractedProduct(name=f"{tag}{i}", confidence=0.9) for i in range(n)])


class CatalogBillingTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="c@example.com", email="c@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-cat-bill")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		for i in range(2):
			Document.objects.create(kb=self.kb, source_type="url", page_url=f"https://shop.test/p{i}", markdown_content=f"page {i}")
		ActionPrice.objects.update_or_create(action="product_import", defaults={"credits": Decimal("2")})
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def fund(self, amount):
		service.grant(self.project, Decimal(amount), "adjust", key="seed")

	def run_extract(self, *pages):
		with mock.patch("knowledge.product_extract._ask", side_effect=list(pages)) as ask:
			saved = extract_candidates(self.kb)
		self.kb.refresh_from_db()
		return saved, ask

	def test_candidates_charged_per_row(self):
		self.fund(100)
		saved, _ = self.run_extract(page(3, "a"), page(3, "b"))
		self.assertEqual(saved, 6)
		keys = set(CreditTransaction.objects.filter(action="product_import").values_list("idempotency_key", flat=True))
		self.assertEqual(keys, {f"product_import:{c.pk}" for c in ProductCandidate.objects.all()})
		self.assertEqual(service.balance_of(self.project), Decimal("88"))
		self.assertEqual(self.kb.last_extraction["status"], "completed")

	def test_partial_page_keeps_affordable(self):
		self.fund(5)
		saved, ask = self.run_extract(page(4), page(4, "b"))
		self.assertEqual(saved, 2)
		self.assertEqual(ask.call_count, 1)
		self.assertEqual(service.balance_of(self.project), Decimal("1"))
		le = self.kb.last_extraction
		self.assertEqual((le["status"], le["found"], le["saved"], le["skipped"]), ("stopped: out of credits", 4, 2, 2))

	def test_concurrent_drop_during_page(self):
		self.fund(10)

		def ask(*a):
			service.grant(self.project, Decimal("-4"), "adjust", key="drop")
			return page(5)

		with mock.patch("knowledge.product_extract._ask", side_effect=ask):
			saved = extract_candidates(self.kb)
		self.assertEqual(saved, 3)
		self.assertEqual(service.balance_of(self.project), Decimal("0"))

	def test_zero_price_keeps_all_candidates(self):
		ActionPrice.objects.filter(action="product_import").update(credits=Decimal("0"))
		self.fund(-10)
		saved, ask = self.run_extract(page(3), page(3, "b"))
		self.assertEqual(saved, 6)
		self.assertEqual(ask.call_count, 2)
		self.assertFalse(CreditTransaction.objects.filter(action="product_import").exists())
		self.assertEqual(self.kb.last_extraction["status"], "completed")

	def test_extract_view_402(self):
		self.fund(1)
		with mock.patch("knowledge.tasks.extract_product_candidates.delay") as delay:
			r = self.client.post("/api/kb/products/extract/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)}, format="json")
		self.assertEqual(r.status_code, 402)
		self.assertEqual(r.json()["error"]["code"], "insufficient_credits")
		delay.assert_not_called()

	def test_rerun_charges_again(self):
		from orders.setup import _products
		self.fund(100)
		with mock.patch("knowledge.product_extract._ask", side_effect=[page(2), page(2, "b"), page(2), page(2, "b")]):
			_products(str(self.project.id), str(self.kb.id), lambda t: None)
			msg = _products(str(self.project.id), str(self.kb.id), lambda t: None)
		self.assertEqual(msg[1], "Found 4 products to review")
		self.assertEqual(ProductCandidate.objects.count(), 4)
		self.assertEqual(CreditTransaction.objects.filter(action="product_import").count(), 8)
		self.assertEqual(service.balance_of(self.project), Decimal("84"))

	def test_candidate_list_includes_last_extraction(self):
		self.kb.last_extraction = {"status": "completed", "saved": 1}
		self.kb.save()
		r = self.client.get("/api/kb/products/candidates/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.json()["data"]["last_extraction"], {"status": "completed", "saved": 1})
