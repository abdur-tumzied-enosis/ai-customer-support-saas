import uuid
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from accounts.models import Project
from knowledge.models import MAX_PRODUCT_IMAGE_BYTES, KnowledgeBase, Product, ProductImage, product_image_path
from knowledge.tasks import embed_product_image
from usage.models import UsageEvent



class ProductImageTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abce")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-prod")
		self.product = Product.objects.create(kb=self.kb, name="Shoe")
		self.image = ProductImage(product=self.product)
		self.image.file.save(product_image_path(self.kb, str(self.image.id), "png"), ContentFile(b"img"), save=True)
		self.embed = mock.patch("knowledge.tasks.embed_image", return_value=[1, 0, 0, 0]).start()
		self.load = mock.patch("knowledge.tasks.load_image", return_value="IMG").start()
		self.upsert = mock.patch("knowledge.tasks.upsert_image").start()
		self.delete_images = mock.patch("knowledge.models.delete_images").start()
		self.addCleanup(mock.patch.stopall)

	def test_image_collection_name(self):
		self.assertEqual(self.kb.image_collection, "vc-prod_images")

	def test_embed_marks_ready_upserts_and_meters(self):
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual(self.image.status, "ready")
		self.load.assert_called_once_with(b"img", MAX_PRODUCT_IMAGE_BYTES)
		self.embed.assert_called_once_with("IMG")
		self.upsert.assert_called_once_with(self.kb.image_collection, str(self.image.id), [1, 0, 0, 0], str(self.product.id))
		self.assertEqual(UsageEvent.objects.filter(kind="image", metadata__op="index").count(), 1)

	def test_embed_failure_marks_failed(self):
		self.embed.side_effect = RuntimeError("boom")
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual((self.image.status, self.image.error), ("failed", "RuntimeError"))
		self.assertEqual(UsageEvent.objects.filter(kind="image").count(), 0)

	def test_deleted_image_is_noop(self):
		embed_product_image(str(uuid.uuid4()))
		self.upsert.assert_not_called()

	def test_image_deleted_during_embed_removes_point_and_skips_metering(self):
		image_id = str(self.image.id)
		self.upsert.side_effect = lambda *a: ProductImage.objects.filter(id=image_id).delete()
		with mock.patch("knowledge.tasks.delete_images") as task_delete:
			embed_product_image(image_id)
		task_delete.assert_called_once_with(self.kb.image_collection, [image_id])
		self.assertEqual(UsageEvent.objects.filter(kind="image").count(), 0)

	def test_product_delete_removes_points_and_files(self):
		path = self.image.file.name
		self.product.delete()
		self.delete_images.assert_called_once_with(self.kb.image_collection, [str(self.image.id)])
		self.assertFalse(default_storage.exists(path))
		self.assertFalse(ProductImage.objects.exists())

	def test_image_delete_removes_point_and_file(self):
		path, image_id = self.image.file.name, str(self.image.id)
		self.image.delete()
		self.delete_images.assert_called_once_with(self.kb.image_collection, [image_id])
		self.assertFalse(default_storage.exists(path))
