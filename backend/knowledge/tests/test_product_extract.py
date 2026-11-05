from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import Project
from knowledge.models import Document, IgnoredProductPage, KnowledgeBase, Product, ProductCandidate
from knowledge.product_extract import ExtractedProduct, ExtractionResult
from knowledge.tasks import extract_product_candidates


def fake_llm(*products):
	"""A get_llm stand-in whose structured call returns `products` for every page."""
	raw = SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}, response_metadata={})
	llm = MagicMock()
	structured = llm.with_structured_output.return_value
	structured.invoke.return_value = {"raw": raw, "parsed": ExtractionResult(products=list(products)), "parsing_error": None}
	return llm


class ProductExtractTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abce")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-a8")
		self.doc = Document.objects.create(
			kb=self.kb, source_type="url", page_url="https://shop.test/p1", markdown_content="# Shirt\nPrice 500 Tk",
			images_metadata=[{"src": "https://shop.test/a.jpg"}, {"src": "https://shop.test/b.jpg"}],
		)

	def run_extract(self, *products, llm=None):
		llm = llm or fake_llm(*products)
		with patch("knowledge.product_extract.get_llm", return_value=llm) as g, \
			patch("knowledge.product_extract.track_token_usage") as track:
			n = extract_product_candidates(str(self.kb.id))
		return n, g, llm, track

	def test_extraction_creates_candidates_only(self):
		before = Product.objects.count()
		n, *_ = self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk", confidence=0.9))
		self.assertEqual(n, 1)
		self.assertEqual(ProductCandidate.objects.count(), 1)
		self.assertEqual(Product.objects.count(), before)

	def test_ignored_page_skipped(self):
		IgnoredProductPage.objects.create(kb=self.kb, url="https://shop.test/p1")
		n, g, llm, _ = self.run_extract(ExtractedProduct(name="Shirt"))
		self.assertEqual(n, 0)
		self.assertEqual(ProductCandidate.objects.count(), 0)
		llm.with_structured_output.return_value.invoke.assert_not_called()

	def test_image_src_not_on_page_dropped(self):
		self.run_extract(ExtractedProduct(name="Shirt", image_srcs=["https://shop.test/a.jpg", "https://evil.test/x.jpg"]))
		self.assertEqual(ProductCandidate.objects.get().payload["image_srcs"], ["https://shop.test/a.jpg"])

	def test_unchanged_match_not_listed(self):
		Product.objects.create(kb=self.kb, name="Shirt", price=Decimal("500"), status="active")
		n, *_ = self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk"))
		self.assertEqual(n, 0)
		self.assertEqual(ProductCandidate.objects.count(), 0)

	def test_price_change_recorded(self):
		p = Product.objects.create(kb=self.kb, name="Shirt", price=Decimal("450"), status="active")
		self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk"))
		c = ProductCandidate.objects.get()
		self.assertEqual(c.matched_product, p)
		self.assertEqual(c.changes, {"price": {"old": "450", "new": "500"}})

	def test_locked_field_not_in_changes(self):
		Product.objects.create(kb=self.kb, name="Shirt", price=Decimal("450"), status="active", locked_fields=["price"])
		n, *_ = self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk"))
		self.assertEqual(n, 0)  # the only difference is locked, so nothing to review
		Product.objects.update(description="old")
		self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk", description="new"))
		self.assertEqual(ProductCandidate.objects.get().changes, {"description": {"old": "old", "new": "new"}})

	def test_tokens_tracked_with_source(self):
		_, _, _, track = self.run_extract(ExtractedProduct(name="Shirt"))
		kinds = [c.args[1] for c in track.call_args_list]
		self.assertEqual(kinds, ["chat_in", "chat_out"])
		for c in track.call_args_list:
			self.assertEqual(c.args[3]["source"], "product_extract")
			self.assertEqual(c.args[3]["kb_id"], str(self.kb.id))
			self.assertEqual(c.args[3]["document_id"], str(self.doc.id))

	def test_price_text_parsed_server_side(self):
		p = ExtractedProduct(name="Shirt", price_text="৳ 1,250", confidence=5)
		self.run_extract(p)
		c = ProductCandidate.objects.get()
		self.assertEqual(c.payload["price"], "1250")
		self.assertEqual(c.confidence, 1.0)  # clamped
		self.assertNotIn("price", ExtractedProduct.model_fields)  # the model has no numeric price to trust

	def test_empty_page_skipped(self):
		self.doc.markdown_content = ""
		self.doc.save()
		n, _, llm, _ = self.run_extract(ExtractedProduct(name="Shirt"))
		self.assertEqual(n, 0)
		llm.with_structured_output.return_value.invoke.assert_not_called()


def llm_returning(*results):
	"""Each structured call returns the next item: a list of products, or an Exception to raise."""
	raw = SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1}, response_metadata={})
	llm = MagicMock()

	def invoke(_msgs):
		r = next(it)
		if isinstance(r, Exception):
			raise r
		return {"raw": raw, "parsed": ExtractionResult(products=list(r)), "parsing_error": None}

	it = iter(results)
	llm.with_structured_output.return_value.invoke.side_effect = invoke
	return llm


class ProductExtractHardeningTest(ProductExtractTest):
	def second_doc(self, **kw):
		return Document.objects.create(
			kb=self.kb, source_type="url", page_url="https://shop.test/p2", markdown_content="# Pants", **kw)

	def test_ignored_pages_do_not_use_page_budget(self):
		from datetime import timedelta

		from django.utils import timezone
		from knowledge.product_extract import MAX_PAGES_PER_RUN
		Document.objects.filter(id=self.doc.id).update(created_at=timezone.now() + timedelta(days=1))
		for i in range(MAX_PAGES_PER_RUN):
			url = f"https://shop.test/ig{i}"
			Document.objects.create(kb=self.kb, source_type="url", page_url=url, markdown_content="x")
			IgnoredProductPage.objects.create(kb=self.kb, url=url)
		n, _, llm, _ = self.run_extract(ExtractedProduct(name="Shirt"))
		self.assertEqual(n, 1)
		self.assertEqual(llm.with_structured_output.return_value.invoke.call_count, 1)

	def test_rerun_does_not_duplicate_pending(self):
		self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk"))
		n, *_ = self.run_extract(ExtractedProduct(name="Shirt", price_text="500 Tk"))
		self.assertEqual(n, 0)
		self.assertEqual(ProductCandidate.objects.count(), 1)

	def test_all_pages_failing_raises(self):
		self.second_doc()
		llm = llm_returning(RuntimeError("bad key"), RuntimeError("bad key"))
		with self.assertRaises(RuntimeError):
			self.run_extract(llm=llm)

	def test_one_failed_page_does_not_stop_next(self):
		self.second_doc()
		llm = llm_returning(RuntimeError("boom"), [ExtractedProduct(name="Pants")])
		n, *_ = self.run_extract(llm=llm)
		self.assertEqual(n, 1)
		self.assertEqual(ProductCandidate.objects.get().name, "Pants")

	def test_malformed_images_metadata_counts_as_failed_page(self):
		Document.objects.filter(id=self.doc.id).update(images_metadata=[{"src": ["not", "a", "string"]}])
		self.second_doc()
		llm = llm_returning([ExtractedProduct(name="Shirt")], [ExtractedProduct(name="Pants")])
		n, *_ = self.run_extract(llm=llm)
		self.assertEqual(n, 1)
		self.assertEqual(ProductCandidate.objects.get().name, "Pants")

	def test_long_sku_truncated(self):
		self.run_extract(ExtractedProduct(name="Shirt", sku="s" * 200))
		self.assertEqual(len(ProductCandidate.objects.get().payload["sku"]), 128)

	def test_variants_capped_at_20(self):
		from knowledge.product_extract import ExtractedVariant
		vs = [ExtractedVariant(label=f"V{i}") for i in range(25)]
		self.run_extract(ExtractedProduct(name="Shirt", variants=vs))
		self.assertEqual(len(ProductCandidate.objects.get().payload["variants"]), 20)

	def test_candidates_per_page_capped(self):
		self.run_extract(*[ExtractedProduct(name=f"P{i}") for i in range(35)])
		self.assertEqual(ProductCandidate.objects.count(), 30)
