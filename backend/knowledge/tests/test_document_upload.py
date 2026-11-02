import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from core.tests.utils import fresh_storage
from knowledge.models import MAX_DOCUMENT_FILE_SIZE, Document, IngestionJob, KnowledgeBase

User = get_user_model()
URL = "/api/kb/documents/upload/"


def fake_presign(key, content_type, sha256_hex=None):
	return {"url": f"https://r2.test/{key}", "headers": {"Content-Type": content_type, "x-amz-checksum-sha256": "b64"}}


@fresh_storage
class DocumentUploadViewTests(TestCase):
	"""Presign (dedupe, validate, `uploading` row) -> browser PUT -> confirm (start ingestion)."""

	def setUp(self):
		self.client = APIClient()
		self.user = User.objects.create_user(username="testuser@example.com", email="testuser@example.com", password="testpass123")
		self.project = Project.objects.create(name="Test Project", slug="test-project-123")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client.force_authenticate(user=self.user)
		p = patch("knowledge.views.presign_put", side_effect=fake_presign)
		self.presign_put = p.start()
		self.addCleanup(p.stop)
		p = patch("knowledge.views.run_ingestion_job.delay")
		self.run_job = p.start()
		self.addCleanup(p.stop)

	def presign(self, content=b"Test PDF content", filename="test.pdf", content_type="application/pdf", **overrides):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "filename": filename,
			"size": len(content), "content_type": content_type, "sha256": hashlib.sha256(content).hexdigest()}
		data.update(overrides)
		return self.client.post(URL, {k: v for k, v in data.items() if v is not None}, format="json")

	def confirm(self, doc_id):
		return self.client.post(f"/api/kb/documents/{doc_id}/confirm/", format="json")

	def upload(self, content=b"Test PDF content", **kw):
		"""Presign + PUT + confirm, like the browser."""
		r = self.presign(content, **kw)
		self.assertEqual(r.status_code, 201, r.content)
		doc = Document.objects.get(id=r.json()["data"]["document_id"])
		default_storage.save(doc.file.name, ContentFile(content))
		return self.confirm(doc.id)

	def test_presign_creates_uploading_document(self):
		content = b"Test PDF content for upload"
		r = self.presign(content)
		self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
		data = r.json()["data"]
		doc = Document.objects.get(id=data["document_id"])
		self.assertEqual((doc.status, doc.filename, doc.file_size, doc.kb), ("uploading", "test.pdf", len(content), self.kb))
		self.assertEqual(doc.checksum, hashlib.sha256(content).hexdigest())
		self.assertEqual(doc.file.name, f"documents/test-project-123/{self.kb.id.hex}/{doc.id}_test.pdf")
		self.assertEqual(data["url"], f"https://r2.test/{doc.file.name}")
		self.assertEqual(data["headers"]["Content-Type"], "application/pdf")
		self.presign_put.assert_called_once_with(doc.file.name, "application/pdf", hashlib.sha256(content).hexdigest())
		self.assertFalse(IngestionJob.objects.exists())

	def test_markdown_without_browser_mime_type(self):
		r = self.presign(b"# Hi", filename="notes.md", content_type="")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(r.json()["data"]["headers"]["Content-Type"], "text/markdown")

	def test_presign_duplicate_409(self):
		content = b"Duplicate test content"
		Document.objects.create(kb=self.kb, filename="existing.pdf", file_size=len(content),
			checksum=hashlib.sha256(content).hexdigest(), status="completed")
		r = self.presign(content, filename="new.pdf")
		self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
		self.assertEqual(r.json()["error"]["code"], "duplicate_file")
		self.assertIn("existing.pdf", r.json()["error"]["message"])
		self.assertEqual(Document.objects.count(), 1)

	def test_presign_bad_checksum_400(self):
		for bad in ("xyz", "A" * 64, None):
			with self.subTest(sha256=bad):
				r = self.presign(sha256=bad)
				self.assertEqual(r.status_code, 400)
				self.assertEqual(r.json()["error"]["code"], "invalid_checksum")
		self.assertFalse(Document.objects.exists())

	def test_presign_too_large_400(self):
		r = self.presign(size=MAX_DOCUMENT_FILE_SIZE + 1)
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "file_too_large")

	def test_presign_missing_or_bad_size_400(self):
		for size in (None, 0, "10"):
			with self.subTest(size=size):
				r = self.presign(size=size)
				self.assertEqual(r.status_code, 400)
				self.assertEqual(r.json()["error"]["code"], "no_file")

	def test_presign_missing_project_id(self):
		r = self.presign(project_id=None)
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "missing_project")

	def test_presign_invalid_file_type(self):
		r = self.presign(filename="test.doc", content_type="application/msword")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_file_type")

	def test_presign_no_access_to_project(self):
		other = User.objects.create_user(username="other@example.com", email="other@example.com", password="pass123")
		self.client.force_authenticate(user=other)
		r = self.presign()
		self.assertEqual(r.status_code, 403)
		self.assertEqual(r.json()["error"]["code"], "access_denied")

	def test_presign_unauthenticated(self):
		self.client.force_authenticate(user=None)
		self.assertEqual(self.presign().status_code, 403)

	def test_presign_with_malformed_project_id_is_403(self):
		self.assertEqual(self.presign(project_id="not-a-uuid").status_code, 403)

	def test_presign_goes_to_chosen_kb(self):
		other = KnowledgeBase.objects.create(project=self.project, name="Products")
		self.assertEqual(self.presign(kb_id=str(other.id)).status_code, 201)
		self.assertEqual(Document.objects.get().kb_id, other.id)

	def test_presign_without_kb_id_uses_default(self):
		self.assertEqual(self.presign(kb_id=None).status_code, 201)
		self.assertEqual(Document.objects.get().kb_id, self.kb.id)

	def test_presign_rejects_foreign_or_malformed_kb_id(self):
		foreign = KnowledgeBase.objects.create(project=Project.objects.create(name="Other", slug="other-proj"), name="x")
		for kb_id in (str(foreign.id), "not-a-uuid"):
			self.assertEqual(self.presign(kb_id=kb_id).status_code, 404)
		self.assertFalse(Document.objects.exists())

	def test_long_filename_is_truncated_keeping_extension(self):
		r = self.presign(filename="x" * 150 + ".pdf")
		doc = Document.objects.get(id=r.json()["data"]["document_id"])
		self.assertEqual(len(doc.filename), 100)
		self.assertTrue(doc.filename.endswith(".pdf"))

	def test_upload_to_long_kb_name_in_signup_sized_project(self):
		project = Project.objects.create(name="Real", slug="0123456789abcdef0123456789abcd")  # signup slugs are 30 hex chars
		ProjectUser.objects.create(project=project, user=self.user, role="admin")
		kb = KnowledgeBase.objects.create(project=project, name="Support policies and refund rules for 2026")
		r = self.upload(filename="refund-policy-for-customers.pdf", project_id=str(project.id), kb_id=str(kb.id))
		self.assertEqual(r.status_code, 201, r.content)
		doc = Document.objects.get(kb=kb)
		self.assertIn(kb.id.hex, doc.file.name)
		self.assertTrue(doc.file.name.endswith("refund-policy-for-customers.pdf"))

	def test_confirm_starts_ingestion(self):
		r = self.upload(b"Test text content", filename="test.txt", content_type="text/plain")
		self.assertEqual(r.status_code, 201, r.content)
		data = r.json()["data"]
		doc = Document.objects.get()
		self.assertEqual(doc.status, "processing")
		job = IngestionJob.objects.get(document=doc)
		self.assertEqual(job.status, "pending")
		self.assertEqual(job.celery_task_kwargs, {"task": "run_ingestion_job", "kwargs": {"job_id": str(job.id)}})
		self.run_job.assert_called_once_with(str(job.id))
		self.assertEqual(data["document"]["id"], str(doc.id))
		self.assertEqual(data["ingestion_job"], {"id": str(job.id), "status": "pending"})
		self.assertEqual(set(data["kb"]), {"id", "name", "project_id", "project_name"})
		self.assertEqual(set(data["document"]), {"id", "filename", "file_size", "status", "created_at"})

	def test_confirm_missing_object_marks_failed(self):
		doc_id = self.presign().json()["data"]["document_id"]
		r = self.confirm(doc_id)
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "upload_missing")
		self.assertEqual(Document.objects.get().status, "failed")
		self.run_job.assert_not_called()

	def test_confirm_oversize_marks_failed(self):
		doc = Document.objects.get(id=self.presign().json()["data"]["document_id"])
		default_storage.save(doc.file.name, ContentFile(b"x" * 101))
		with patch("knowledge.views.MAX_DOCUMENT_FILE_SIZE", 100):
			r = self.confirm(doc.id)
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "upload_too_large")
		self.assertEqual(Document.objects.get().status, "failed")
		self.assertFalse(default_storage.exists(doc.file.name))
		self.run_job.assert_not_called()

	def test_confirm_twice_creates_one_job(self):
		doc = Document.objects.get(id=self.presign().json()["data"]["document_id"])
		default_storage.save(doc.file.name, ContentFile(b"Test PDF content"))
		first = self.confirm(doc.id)
		second = self.confirm(doc.id)
		self.assertEqual((first.status_code, second.status_code), (201, 200))
		self.assertEqual(IngestionJob.objects.count(), 1)
		self.assertEqual(second.json()["data"]["ingestion_job"]["id"], first.json()["data"]["ingestion_job"]["id"])
		self.run_job.assert_called_once()

	def test_confirm_other_project_404(self):
		doc_id = self.presign().json()["data"]["document_id"]
		other = User.objects.create_user(username="o@example.com", email="o@example.com", password="x")
		self.client.force_authenticate(user=other)
		self.assertEqual(self.confirm(doc_id).status_code, 404)
		self.assertEqual(Document.objects.get().status, "uploading")

	def test_document_list_rejects_malformed_ids(self):
		response = self.client.get("/api/kb/documents/", {"project_id": "not-a-uuid"})
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		response = self.client.get("/api/kb/documents/", {"project_id": str(self.project.id), "kb_id": "not-a-uuid"})
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
