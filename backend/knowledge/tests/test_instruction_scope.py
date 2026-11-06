from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.tasks import get_effective_instructions
from knowledge.models import CustomInstruction, KnowledgeBase

User = get_user_model()


class EffectiveInstructionsTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-scope")
		self.products = KnowledgeBase.objects.create(project=self.project, name="Products")
		self.faq = KnowledgeBase.objects.create(project=self.project, name="FAQ")
		CustomInstruction.objects.create(project=self.project, label="polite", message="Be polite")
		CustomInstruction.objects.create(project=self.project, knowledge_base=self.products, label="warranty", message="Mention warranty")

	def labels(self, kb):
		return [i["label"] for i in get_effective_instructions(str(self.project.id), str(kb.id))]

	def test_kb_gets_shared_then_its_own(self):
		assert self.labels(self.products) == ["polite", "warranty"]  # KB-specific last: it wins on conflicts

	def test_other_kb_gets_only_shared(self):
		assert self.labels(self.faq) == ["polite"]


class InstructionScopeTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="s@example.com", email="s@example.com", password="x")
		self.project = Project.objects.create(name="S", slug="p-scope-2")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products")
		self.faq = KnowledgeBase.objects.create(project=self.project, name="FAQ")
		CustomInstruction.objects.create(project=self.project, label="general", message="g")
		CustomInstruction.objects.create(project=self.project, label="order", message="o", scope="order")
		CustomInstruction.objects.create(project=self.project, label="off", message="f", scope="off")
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_effective_excludes_order_and_off_scopes(self):
		labels = [i.label for i in CustomInstruction.effective(str(self.project.id))]
		assert labels == ["general"]

	def test_effective_order_instructions_returns_order_only(self):
		CustomInstruction.objects.create(project=self.project, knowledge_base=self.kb, label="kb order", message="x", scope="order")
		labels = [i.label for i in CustomInstruction.effective_order_instructions(str(self.project.id), str(self.kb.id))]
		assert labels == ["order", "kb order"]  # shared first, KB's own last, same rule as general
		labels = [i.label for i in CustomInstruction.effective_order_instructions(str(self.project.id), str(self.faq.id))]
		assert labels == ["order"]

	def test_resolve_includes_all_scopes(self):
		r = self.client.get("/api/kb/instructions/resolve/", {"project_id": str(self.project.id), "knowledge_base_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual({i["scope"] for i in r.json()["data"]["instructions"]}, {"general", "order", "off"})

	def test_patch_sets_scope_and_rejects_unknown(self):
		row = CustomInstruction.objects.get(label="general")
		r = self.client.patch(f"/api/kb/instructions/{row.id}/", {"scope": "order"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		row.refresh_from_db()
		self.assertEqual(row.scope, "order")
		r = self.client.patch(f"/api/kb/instructions/{row.id}/", {"scope": "admin"}, format="json")
		self.assertEqual(r.status_code, 400, r.content)
		row.refresh_from_db()
		self.assertEqual(row.scope, "order")
