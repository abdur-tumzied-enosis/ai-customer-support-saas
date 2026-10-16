import os
import hashlib
from io import BytesIO
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status
from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Document, IngestionJob, DocumentFileType, MAX_DOCUMENT_FILE_SIZE

User = get_user_model()


class DocumentUploadViewTests(TestCase):
	"""Test cases for document upload functionality"""
	
	def setUp(self):
		"""Set up test client, user, and project"""
		self.client = APIClient()
		
		# Create test user
		self.user = User.objects.create_user(
			username="testuser@example.com",
			email="testuser@example.com",
			password="testpass123",
			first_name="Test",
			last_name="User"
		)
		
		# Create test project
		self.project = Project.objects.create(name="Test Project", slug="test-project-123")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		
		# Create knowledge base
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		
		# Authenticate client
		self.client.force_authenticate(user=self.user)
	
	def create_test_file(self, filename="test.pdf", content=b"Test PDF content", content_type="application/pdf"):
		"""Helper to create a test file"""
		return SimpleUploadedFile(filename, content, content_type=content_type)
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_success_pdf(self, mock_task):
		"""Test successful PDF document upload"""
		file_content = b"Test PDF content for upload"
		file = self.create_test_file("test.pdf", file_content, "application/pdf")
		
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		# Check response structure
		self.assertTrue(response.data["success"])
		self.assertEqual(response.data["message"], "Document uploaded successfully and processing started")
		self.assertIn("data", response.data)
		self.assertIn("document", response.data["data"])
		self.assertIn("kb", response.data["data"])
		self.assertIn("ingestion_job", response.data["data"])
		
		# Check document was created
		document = Document.objects.get(filename="test.pdf")
		self.assertEqual(document.filename, "test.pdf")
		self.assertEqual(document.file_size, len(file_content))
		self.assertEqual(document.status, "processing")
		self.assertEqual(document.kb, self.kb)
		
		# Check ingestion job was created
		ingestion_job = IngestionJob.objects.filter(document=document).first()
		self.assertIsNotNone(ingestion_job)
		self.assertEqual(ingestion_job.status, "pending")
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_success_txt(self, mock_task):
		"""Test successful TXT document upload"""
		file_content = b"Test text content for upload"
		file = self.create_test_file("test.txt", file_content, "text/plain")
		
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		document = Document.objects.get(filename="test.txt")
		self.assertEqual(document.filename, "test.txt")
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_success_md(self, mock_task):
		"""Test successful Markdown document upload"""
		file_content = b"# Test Markdown\n\nThis is a test."
		file = self.create_test_file("test.md", file_content, "text/markdown")
		
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		document = Document.objects.get(filename="test.md")
		self.assertEqual(document.filename, "test.md")
	
	def test_upload_missing_project_id(self):
		"""Test upload without project_id"""
		file = self.create_test_file("test.pdf", b"content", "application/pdf")
		
		data = {
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "missing_project")
	
	def test_upload_missing_file(self):
		"""Test upload without file"""
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id)
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "no_file")
	
	def test_upload_invalid_file_type(self):
		"""Test upload with unsupported file type"""
		file = self.create_test_file("test.doc", b"content", "application/msword")
		
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "invalid_file_type")
	
	def test_upload_file_too_large(self):
		"""Test upload with file exceeding size limit"""
		# Create a file larger than MAX_DOCUMENT_FILE_SIZE
		large_content = b"x" * (MAX_DOCUMENT_FILE_SIZE + 1)
		file = self.create_test_file("large.pdf", large_content, "application/pdf")
		
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "file_too_large")
	
	def test_upload_duplicate_file(self):
		"""Test upload of duplicate file (same checksum)"""
		file_content = b"Duplicate test content"
		checksum = hashlib.sha256(file_content).hexdigest()
		
		# Create existing document with same checksum
		existing_doc = Document.objects.create(
			kb=self.kb,
			filename="existing.pdf",
			file_size=len(file_content),
			checksum=checksum,
			status="completed"
		)
		
		# Try to upload same file
		file = self.create_test_file("new.pdf", file_content, "application/pdf")
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "duplicate_file")
	
	def test_upload_no_access_to_project(self):
		"""Test upload when user doesn't have admin access to project"""
		# Create another user without access
		other_user = User.objects.create_user(
			username="other@example.com",
			email="other@example.com",
			password="pass123"
		)
		self.client.force_authenticate(user=other_user)
		
		file = self.create_test_file("test.pdf", b"content", "application/pdf")
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		self.assertFalse(response.data["success"])
		self.assertEqual(response.data["error"]["code"], "access_denied")
	
	def test_upload_unauthenticated(self):
		"""Test upload without authentication"""
		self.client.force_authenticate(user=None)
		
		file = self.create_test_file("test.pdf", b"content", "application/pdf")
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		# DRF returns 403 Forbidden for unauthenticated requests with IsAuthenticated permission
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_goes_to_chosen_kb(self, mock_task):
		other = KnowledgeBase.objects.create(project=self.project, name="Products")
		data = {"project_id": str(self.project.id), "kb_id": str(other.id), "file": self.create_test_file()}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Document.objects.get().kb_id, other.id)

	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_without_kb_id_uses_default(self, mock_task):
		data = {"project_id": str(self.project.id), "file": self.create_test_file()}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Document.objects.get().kb_id, self.kb.id)

	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_rejects_foreign_or_malformed_kb_id(self, mock_task):
		foreign = KnowledgeBase.objects.create(project=Project.objects.create(name="Other", slug="other-proj"), name="x")
		for kb_id in (str(foreign.id), "not-a-uuid"):
			data = {"project_id": str(self.project.id), "kb_id": kb_id, "file": self.create_test_file()}
			response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
			self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertFalse(Document.objects.exists())
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_checksum_calculation(self, mock_task):
		"""Test that checksum is correctly calculated"""
		file_content = b"Test content for checksum"
		expected_checksum = hashlib.sha256(file_content).hexdigest()
		
		file = self.create_test_file("test.pdf", file_content, "application/pdf")
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		document = Document.objects.get(filename="test.pdf")
		self.assertEqual(document.checksum, expected_checksum)
	
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_response_data_structure(self, mock_task):
		"""Test that response contains all required fields"""
		file = self.create_test_file("test.pdf", b"content", "application/pdf")
		data = {
			"project_id": str(self.project.id),
			"kb_id": str(self.kb.id),
			"file": file
		}
		
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		# Check document data
		doc_data = response.data["data"]["document"]
		self.assertIn("id", doc_data)
		self.assertIn("filename", doc_data)
		self.assertIn("file_size", doc_data)
		self.assertIn("status", doc_data)
		self.assertIn("created_at", doc_data)
		
		# Check KB data
		kb_data = response.data["data"]["kb"]
		self.assertIn("id", kb_data)
		self.assertIn("name", kb_data)
		self.assertIn("project_id", kb_data)
		self.assertIn("project_name", kb_data)
		
		# Check ingestion job data
		job_data = response.data["data"]["ingestion_job"]
		self.assertIn("id", job_data)
		self.assertIn("status", job_data)


	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_to_long_kb_name_in_signup_sized_project(self, mock_task):
		project = Project.objects.create(name="Real", slug="0123456789abcdef0123456789abcd")  # signup slugs are 30 hex chars
		ProjectUser.objects.create(project=project, user=self.user, role="admin")
		kb = KnowledgeBase.objects.create(project=project, name="Support policies and refund rules for 2026")
		file = self.create_test_file("refund-policy-for-customers.pdf")
		data = {"project_id": str(project.id), "kb_id": str(kb.id), "file": file}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		doc = Document.objects.get(kb=kb)
		self.assertIn(kb.id.hex, doc.file.name)
		self.assertTrue(doc.file.name.endswith("refund-policy-for-customers.pdf"))

	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_with_malformed_project_id_is_403(self, mock_task):
		data = {"project_id": "not-a-uuid", "file": self.create_test_file()}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_document_list_rejects_malformed_ids(self):
		response = self.client.get("/api/kb/documents/", {"project_id": "not-a-uuid"})
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
		response = self.client.get("/api/kb/documents/", {"project_id": str(self.project.id), "kb_id": "not-a-uuid"})
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
