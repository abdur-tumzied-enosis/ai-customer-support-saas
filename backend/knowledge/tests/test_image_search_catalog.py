from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import Project
from channels_app import image_reply
from knowledge.models import KnowledgeBase, Product


@override_settings(IMAGE_SEARCH_ENABLED=False)
class ImageSearchCatalogTests(TestCase):
	def test_image_search_excludes_non_active(self):
		project = Project.objects.create(name="P", slug="p-img-cat")
		kb = KnowledgeBase.objects.create(project=project, name="default", is_default=True)
		rows = {s: Product.objects.create(kb=kb, name=s, status=s, price=100) for s in ("active", "hidden", "needs_info")}
		hits = [(str(p.id), 0.9) for p in rows.values()]
		conv = SimpleNamespace(id="c1", knowledge_base=kb, project_id=project.id, channel="widget")
		msg = SimpleNamespace(id="m1")
		with mock.patch.object(image_reply, "search_products", return_value=hits), \
			mock.patch.object(image_reply, "embed_image"), mock.patch.object(image_reply, "load_image"), \
			mock.patch.object(image_reply, "fetch_photo"), mock.patch.object(image_reply, "record_image_usage"):
			note = image_reply._photo_note(conv, msg, {})
		self.assertIn('"active"', note)
		self.assertNotIn('"hidden"', note)
		self.assertNotIn('"needs_info"', note)
