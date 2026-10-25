from django.test import TestCase

from accounts.models import Project
from channels_app.tasks import get_effective_instructions
from knowledge.models import CustomInstruction, KnowledgeBase


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
