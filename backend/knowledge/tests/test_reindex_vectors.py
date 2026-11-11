from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Project
from knowledge.models import Document, KnowledgeBase, Product, ProductImage

CMD = "knowledge.management.commands.reindex_vectors"


class ReindexVectorsTests(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p")
		self.kb = KnowledgeBase.objects.create(project=project, name="default", vector_collection="kb_p")
		self.upload = Document.objects.create(kb=self.kb, filename="a.txt", vector_chunk_ids=["old1"])
		self.page = Document.objects.create(kb=self.kb, source_type="url", page_url="https://x", vector_chunk_ids=["old2"])
		self.broken = Document.objects.create(kb=self.kb, filename="gone.txt", vector_chunk_ids=["old3"])
		Document.objects.create(kb=self.kb, filename="never-indexed.txt")

	@patch(f"{CMD}.embed_crawled_document")
	@patch(f"{CMD}.embed_uploaded_chunks")
	@patch(f"{CMD}.extract_text_from_file")
	@patch(f"{CMD}.delete_collection")
	def test_rebuilds_both_collections_from_sources(self, delete, extract, embed_upload, embed_page):
		extract.side_effect = lambda d: (_ for _ in ()).throw(FileNotFoundError("missing")) if d.id == self.broken.id else "hello"
		err = StringIO()
		call_command("reindex_vectors", stdout=StringIO(), stderr=err)

		assert [c.args[0] for c in delete.call_args_list] == ["kb_p", "kb_p_web", "kb_p_images"]  # last: legacy photo collection
		embed_page.assert_called_once_with(self.page, "kb_p_web")
		assert [c.args[0] for c in embed_upload.call_args_list] == [self.upload]
		# a document that can't be rebuilt is reported and no longer points at deleted vectors
		self.broken.refresh_from_db()
		assert self.broken.vector_chunk_ids == [] and str(self.broken.id) in err.getvalue()

	@patch(f"{CMD}.embed_product_image")
	@patch(f"{CMD}.embed_crawled_document")
	@patch(f"{CMD}.embed_uploaded_chunks")
	@patch(f"{CMD}.extract_text_from_file", return_value="hello")
	@patch(f"{CMD}.delete_collection")
	def test_requeues_ready_and_pending_photos(self, delete, extract, embed_upload, embed_page, embed_image):
		product = Product.objects.create(kb=self.kb, name="Shirt")
		ready = ProductImage.objects.create(product=product, file="a.jpg", status="ready")
		pending = ProductImage.objects.create(product=product, file="b.jpg", status="pending")
		ProductImage.objects.create(product=product, file="c.jpg", status="failed")
		ProductImage.objects.create(product=product, file="d.jpg", status="uploading")
		call_command("reindex_vectors", stdout=StringIO(), stderr=StringIO())
		assert {c.args[0] for c in embed_image.delay.call_args_list} == {str(ready.id), str(pending.id)}
		assert embed_image.delay.call_count == 2
