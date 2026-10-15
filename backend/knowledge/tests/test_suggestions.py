import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction, Document, IngestionJob, KnowledgeBase, MemorySuggestion

MEDIA = tempfile.mkdtemp()
URL = "/api/kb/suggestions/"


@override_settings(MEDIA_ROOT=MEDIA)
class SuggestionApiTest(TestCase):
	@classmethod
	def tearDownClass(cls):
		shutil.rmtree(MEDIA, ignore_errors=True)
		super().tearDownClass()

	def setUp(self):
		self.user = get_user_model().objects.create_user(username="k@x.com", email="k@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-sugg")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-sugg")
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		self.ingest = mock.patch("knowledge.suggestions.run_ingestion_job.delay").start()
		self.addCleanup(mock.patch.stopall)

	def suggestion(self, kind="knowledge", **extra):
		return MemorySuggestion.objects.create(
			project=self.project, knowledge_base=self.kb, kind=kind, title="Refund window?", body="30 days.", **extra
		)

	def test_list_returns_pending_for_project(self):
		s = self.suggestion()
		self.suggestion(status="rejected")
		r = self.client.get(f"{URL}?project_id={self.project.id}")
		assert r.status_code == 200, r.content
		assert [x["id"] for x in r.json()["data"]["suggestions"]] == [str(s.id)]

	def test_approve_knowledge_creates_document_and_ingestion(self):
		s = self.suggestion()
		with self.captureOnCommitCallbacks(execute=True):
			r = self.client.post(f"{URL}{s.id}/approve/", {"body": "Within 30 days, with receipt."}, format="json")
		assert r.status_code == 200, r.content
		doc = Document.objects.get(kb=self.kb)
		assert default_storage.open(doc.file.name).read().decode() == "Q: Refund window?\nA: Within 30 days, with receipt.\n"
		job = IngestionJob.objects.get(document=doc)
		self.ingest.assert_called_once_with(str(job.id))
		s.refresh_from_db()
		assert (s.status, s.result_id, s.decided_by) == ("approved", str(doc.id), self.user)

	def test_approve_instruction_follows_kb_level_instructions(self):
		s = self.suggestion(kind="instruction")
		self.client.post(f"{URL}{s.id}/approve/", {}, format="json")
		assert CustomInstruction.objects.get().knowledge_base is None  # no KB-level set: project-level

		CustomInstruction.objects.create(project=self.project, knowledge_base=self.kb, label="x", message="y")
		s2 = self.suggestion(kind="instruction")
		self.client.post(f"{URL}{s2.id}/approve/", {}, format="json")
		inst = CustomInstruction.objects.get(id=MemorySuggestion.objects.get(id=s2.id).result_id)
		assert inst.knowledge_base == self.kb and inst.label == "Refund window?" and inst.message == "30 days."

	def test_reject_and_no_second_decision(self):
		s = self.suggestion()
		assert self.client.post(f"{URL}{s.id}/reject/", format="json").status_code == 200
		assert self.client.post(f"{URL}{s.id}/approve/", {}, format="json").status_code == 409
		assert not Document.objects.exists()

	def test_non_member_cannot_see_or_decide(self):
		s = self.suggestion()
		other = get_user_model().objects.create_user(username="o@x.com", email="o@x.com", password="pw123456")
		self.client.force_authenticate(other)
		assert self.client.get(f"{URL}?project_id={self.project.id}").status_code == 403
		assert self.client.post(f"{URL}{s.id}/approve/", {}, format="json").status_code == 404
		s.refresh_from_db()
		assert s.status == "pending"

	def test_non_admin_member_cannot_decide(self):
		s = self.suggestion()
		viewer = get_user_model().objects.create_user(username="v@x.com", email="v@x.com", password="pw123456")
		ProjectUser.objects.create(project=self.project, user=viewer, role="viewer")
		self.client.force_authenticate(viewer)
		assert self.client.post(f"{URL}{s.id}/approve/", {}, format="json").status_code == 404
		s.refresh_from_db()
		assert s.status == "pending"
