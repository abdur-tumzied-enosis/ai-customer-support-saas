from decimal import Decimal
from agents.embeddings_api import Embedded
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from accounts.models import Project
from billing import service
from core.tests.utils import fresh_storage
from knowledge.models import Document, KnowledgeBase, Product, ProductImage, product_image_path
from knowledge.product_extract import ExtractedProduct, ExtractionResult
from knowledge.tasks import embed_product_image, extract_product_candidates
from usage.models import UsageEvent


@fresh_storage
class CatalogUsageTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abd5")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-a15")
		service.grant(self.project, Decimal("1000"), "adjust", key="seed")
		self.addCleanup(mock.patch.stopall)

	def test_extraction_tracks_chat_in_and_out(self):
		doc = Document.objects.create(kb=self.kb, source_type="url", page_url="https://shop.test/p1", markdown_content="# Shirt")
		raw = SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}, response_metadata={})
		llm = mock.MagicMock()
		llm.with_structured_output.return_value.invoke.return_value = {
			"raw": raw, "parsed": ExtractionResult(products=[ExtractedProduct(name="Shirt")]), "parsing_error": None}
		with mock.patch("knowledge.product_extract.get_llm", return_value=llm):
			extract_product_candidates(str(self.kb.id))
		events = {e.kind: e for e in UsageEvent.objects.filter(project=self.project)}
		self.assertEqual(set(events), {"chat_in", "chat_out"})
		self.assertEqual((events["chat_in"].tokens, events["chat_out"].tokens), (10, 5))
		for e in events.values():
			self.assertEqual(e.metadata["source"], "product_extract")
			self.assertEqual(e.metadata["document_id"], str(doc.id))

	def test_product_embed_recorded(self):
		# existing call: record_image_usage(project_id, "index") -> kind "image", metadata op "index"
		product = Product.objects.create(kb=self.kb, name="Shoe")
		image = ProductImage(product=product)
		buf = BytesIO()
		Image.new("RGB", (20, 10), "red").save(buf, "PNG")
		image.file.name = default_storage.save("tmp/" + product_image_path(self.kb, str(image.id), "png"), ContentFile(buf.getvalue()))
		image.save()
		mock.patch("knowledge.tasks.embed_image", return_value=Embedded([[1, 0, 0, 0]], 1200, Decimal("0.00048"), "cohere/embed-5-fast")).start()
		mock.patch("knowledge.tasks.upsert_image").start()
		mock.patch("knowledge.models.delete_images").start()
		embed_product_image(str(image.id))
		event = UsageEvent.objects.get(project=self.project)
		self.assertEqual((event.kind, event.metadata["op"], event.metadata["image_id"]), ("image", "index", str(image.id)))
