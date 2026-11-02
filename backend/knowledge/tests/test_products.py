import uuid
from datetime import timedelta
from io import BytesIO
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from accounts.models import Project
from core.storage import thumb_key
from core.tests.utils import fresh_storage
from knowledge.models import Document, KnowledgeBase, Product, ProductImage, product_image_path
from knowledge.tasks import cleanup_abandoned_uploads, embed_product_image
from usage.models import UsageEvent


def png(w=2000, h=1000):
	buf = BytesIO()
	Image.new("RGB", (w, h), "red").save(buf, "PNG")
	return buf.getvalue()


@fresh_storage
class ProductImageTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abce")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-prod")
		self.product = Product.objects.create(kb=self.kb, name="Shoe")
		self.image = ProductImage(product=self.product)
		self.tmp = default_storage.save("tmp/" + product_image_path(self.kb, str(self.image.id), "png"), ContentFile(png()))
		self.image.file.name = self.tmp
		self.image.save()
		self.embed = mock.patch("knowledge.tasks.embed_image", return_value=[1, 0, 0, 0]).start()
		self.upsert = mock.patch("knowledge.tasks.upsert_image").start()
		self.delete_images = mock.patch("knowledge.models.delete_images").start()
		self.addCleanup(mock.patch.stopall)

	def test_image_collection_name(self):
		self.assertEqual(self.kb.image_collection, "vc-prod_images")

	def test_embed_marks_ready_upserts_and_meters(self):
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual(self.image.status, "ready")
		self.assertEqual(self.embed.call_args.args[0].size, (1600, 800))  # embedded from the display variant
		self.upsert.assert_called_once_with(self.kb.image_collection, str(self.image.id), [1, 0, 0, 0], str(self.product.id))
		self.assertEqual(UsageEvent.objects.filter(kind="image", metadata__op="index").count(), 1)

	def test_embed_writes_variants_and_drops_tmp(self):
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		name = self.image.file.name
		self.assertEqual(name, product_image_path(self.kb, str(self.image.id), "webp"))
		with default_storage.open(name) as f:
			self.assertEqual(Image.open(f).size, (1600, 800))
		with default_storage.open(thumb_key(name)) as f:
			self.assertEqual(Image.open(f).size, (320, 160))
		self.assertFalse(default_storage.exists(self.tmp))

	def test_embed_of_already_final_image_reembeds_without_resizing(self):
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		name = self.image.file.name
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual(self.image.file.name, name)
		self.assertEqual(self.upsert.call_count, 2)

	def test_non_image_upload_fails_and_is_deleted(self):
		with default_storage.open(self.tmp, "wb") as f:
			f.write(b"%PDF-1.7 not an image")
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual((self.image.status, self.image.error), ("failed", "NotAnImage"))
		self.assertFalse(default_storage.exists(self.tmp))
		self.upsert.assert_not_called()

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
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		path = self.image.file.name
		self.product.delete()
		self.delete_images.assert_called_once_with(self.kb.image_collection, [str(self.image.id)])
		self.assertFalse(default_storage.exists(path))
		self.assertFalse(default_storage.exists(thumb_key(path)))
		self.assertFalse(ProductImage.objects.exists())

	def test_image_delete_removes_point_and_both_files(self):
		embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		path, image_id = self.image.file.name, str(self.image.id)
		self.image.delete()
		self.delete_images.assert_called_once_with(self.kb.image_collection, [image_id])
		self.assertFalse(default_storage.exists(path))
		self.assertFalse(default_storage.exists(thumb_key(path)))

	def test_oversize_tmp_upload_fails_without_reading_it_all(self):
		with mock.patch("knowledge.tasks.MAX_PRODUCT_IMAGE_BYTES", 100):
			embed_product_image(str(self.image.id))
		self.image.refresh_from_db()
		self.assertEqual(self.image.status, "failed")
		self.assertFalse(default_storage.exists(self.tmp))
		self.upsert.assert_not_called()


@fresh_storage
class CleanupAbandonedUploadsTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-cleanup")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-cleanup")
		self.delete_images = mock.patch("knowledge.models.delete_images").start()
		self.addCleanup(mock.patch.stopall)

	def uploading_image(self, product, age):
		img = ProductImage(product=product, status="uploading")
		img.file.name = default_storage.save(f"tmp/x/{img.id}.jpg", ContentFile(b"img"))
		img.save()
		ProductImage.objects.filter(id=img.id).update(created_at=timezone.now() - age)
		return img

	def test_cleanup_removes_abandoned_and_empty_products(self):
		lonely = Product.objects.create(kb=self.kb, name="Lonely")
		old = self.uploading_image(lonely, timedelta(hours=2))
		fresh_product = Product.objects.create(kb=self.kb, name="Fresh")
		fresh = self.uploading_image(fresh_product, timedelta(minutes=10))
		cleanup_abandoned_uploads()
		self.assertFalse(ProductImage.objects.filter(id=old.id).exists())
		self.assertFalse(default_storage.exists(old.file.name))
		self.assertFalse(Product.objects.filter(id=lonely.id).exists())
		self.assertTrue(ProductImage.objects.filter(id=fresh.id).exists())
		self.assertTrue(Product.objects.filter(id=fresh_product.id).exists())

	def test_cleanup_keeps_product_with_other_images(self):
		product = Product.objects.create(kb=self.kb, name="Shoe")
		ProductImage.objects.create(product=product, status="ready", file="products/x/ok.webp")
		self.uploading_image(product, timedelta(hours=2))
		cleanup_abandoned_uploads()
		self.assertTrue(Product.objects.filter(id=product.id).exists())
		self.assertEqual(product.images.count(), 1)

	def test_cleanup_removes_abandoned_documents(self):
		doc = Document.objects.create(kb=self.kb, source_type="upload", filename="a.pdf", status="uploading")
		doc.file.name = default_storage.save(f"documents/x/{doc.id}_a.pdf", ContentFile(b"%PDF"))
		doc.save()
		Document.objects.filter(id=doc.id).update(created_at=timezone.now() - timedelta(hours=2))
		done = Document.objects.create(kb=self.kb, source_type="upload", filename="b.pdf", status="completed")
		Document.objects.filter(id=done.id).update(created_at=timezone.now() - timedelta(hours=2))
		cleanup_abandoned_uploads()
		self.assertFalse(Document.objects.filter(id=doc.id).exists())
		self.assertFalse(default_storage.exists(doc.file.name))
		self.assertTrue(Document.objects.filter(id=done.id).exists())
