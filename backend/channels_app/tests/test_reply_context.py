from django.test import TestCase

from accounts.models import Project
from channels_app.tasks import build_reply_context
from knowledge.models import KnowledgeBase


class ReplyContextTest(TestCase):
	def test_uses_stored_collection_after_rename(self):
		project = Project.objects.create(name="P", slug="p-ctx")
		kb = KnowledgeBase.objects.create(project=project, name="default", vector_collection="kb_p-ctx_default_legacy")
		kb.name = "Products"
		kb.save()
		context, _ = build_reply_context(project, kb)
		assert context.vector_collection == "kb_p-ctx_default_legacy"
