from unittest.mock import patch

from django.test import TestCase

from accounts.models import Project
from knowledge.models import Document, KnowledgeBase

VECTOR_DELETE = "agents.vector_store.delete_vectors_from_collection"


class DocumentDeleteVectorsTests(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p")
		self.kb = KnowledgeBase.objects.create(project=project, name="default", vector_collection="kb_p")
		self.parent = Document.objects.create(kb=self.kb, source_type="url", page_url="https://x")
		self.child1 = Document.objects.create(kb=self.kb, source_type="url", parent_crawl_id=self.parent, vector_chunk_ids=["a", "b"])
		self.child2 = Document.objects.create(kb=self.kb, source_type="url", parent_crawl_id=self.parent, vector_chunk_ids=["c"])

	@patch(VECTOR_DELETE, return_value=True)
	def test_deleting_parent_removes_child_vectors_from_web_collection(self, delete_vectors):
		self.parent.delete()

		delete_vectors.assert_called_once()
		collection, ids = delete_vectors.call_args.kwargs["collection_name"], delete_vectors.call_args.kwargs["vector_ids"]
		assert collection == "kb_p_web"
		assert sorted(ids) == ["a", "b", "c"]
		assert not Document.objects.filter(id__in=[self.parent.id, self.child1.id, self.child2.id]).exists()

	@patch(VECTOR_DELETE, return_value=True)
	def test_deleting_one_child_removes_only_its_vectors(self, delete_vectors):
		self.child1.delete()

		delete_vectors.assert_called_once_with(collection_name="kb_p_web", vector_ids=["a", "b"])
		assert Document.objects.filter(id=self.child2.id).exists()

	@patch(VECTOR_DELETE, return_value=True)
	def test_uploaded_document_uses_kb_collection(self, delete_vectors):
		upload = Document.objects.create(kb=self.kb, filename="a.txt", vector_chunk_ids=["u1"])
		upload.delete()

		delete_vectors.assert_called_once_with(collection_name="kb_p", vector_ids=["u1"])

	@patch(VECTOR_DELETE, side_effect=ConnectionError("qdrant down"))
	def test_qdrant_failure_does_not_block_row_deletion(self, _delete_vectors):
		self.parent.delete()

		assert not Document.objects.filter(kb=self.kb).exists()
