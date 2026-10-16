from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Project
from knowledge.models import KnowledgeBase


class KnowledgeBaseModelTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-kb")

	def test_collection_named_by_id_for_any_name(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="পণ্য তালিকা")
		assert kb.vector_collection == f"kb_p-kb_{kb.id.hex}"
		assert kb.vector_collection.isascii()

	def test_get_default_creates_once_and_survives_rename(self):
		kb = KnowledgeBase.get_default(self.project)
		assert kb.is_default and kb.name == "default"
		kb.name = "Main"
		kb.save()
		assert KnowledgeBase.get_default(self.project).id == kb.id
		assert KnowledgeBase.objects.filter(project=self.project).count() == 1

	def test_names_unique_per_project_ignoring_case(self):
		KnowledgeBase.objects.create(project=self.project, name="Products")
		with self.assertRaises(IntegrityError), transaction.atomic():
			KnowledgeBase.objects.create(project=self.project, name="products")
		other = Project.objects.create(name="Q", slug="q-kb")
		KnowledgeBase.objects.create(project=other, name="Products")  # other project: fine

	def test_get_default_adopts_legacy_default_in_other_case(self):
		legacy = KnowledgeBase.objects.create(project=self.project, name="Default")
		kb = KnowledgeBase.get_default(self.project)
		assert kb.id == legacy.id and kb.is_default
