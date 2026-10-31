import io
import uuid
from unittest import mock

from django.test import SimpleTestCase, override_settings
from PIL import Image
from qdrant_client import QdrantClient, models

from agents import image_search
from agents.image_search import (
	MAX_IMAGE_BYTES, NotAnImage, delete_images, image_format, load_image, search_products, upsert_image,
)


def _png_bytes(w, h):
	buf = io.BytesIO()
	Image.new("RGB", (w, h), "red").save(buf, "PNG")
	return buf.getvalue()


class ImageSearchTest(SimpleTestCase):
	"""In-memory Qdrant with hand-made 4-dim vectors; the real CLIP model is never loaded."""

	def setUp(self):
		client = QdrantClient(":memory:")
		p = mock.patch.object(image_search, "get_qdrant_client", return_value=client)
		p.start()
		self.addCleanup(p.stop)
		self.A1, self.A2, self.B1, self.C1 = (str(uuid.uuid4()) for _ in range(4))

	def test_search_groups_by_product_and_filters_by_score(self):
		upsert_image("c_images", self.A1, [1, 0, 0, 0], "pA")
		upsert_image("c_images", self.A2, [0.9, 0.1, 0, 0], "pA")
		upsert_image("c_images", self.B1, [0.7, 0.7, 0, 0], "pB")
		upsert_image("c_images", self.C1, [0, 0, 1, 0], "pC")
		with override_settings(IMAGE_SEARCH_MIN_SCORE=0.6):
			hits = search_products("c_images", [1, 0, 0, 0])
		self.assertEqual([p for p, _ in hits], ["pA", "pB"])  # pA once, pC below threshold
		self.assertAlmostEqual(hits[0][1], 1.0, places=3)

	def test_search_missing_collection_returns_empty(self):
		self.assertEqual(search_products("nope_images", [1, 0, 0, 0]), [])

	def test_delete_images_removes_points(self):
		upsert_image("c_images", self.A1, [1, 0, 0, 0], "pA")
		delete_images("c_images", [self.A1])
		self.assertEqual(search_products("c_images", [1, 0, 0, 0]), [])
		delete_images("nope_images", [self.A1])  # missing collection: no-op

	def test_load_image_rejects_non_images_and_oversize(self):
		with self.assertRaises(NotAnImage):
			load_image(b"%PDF-1.4 ...")
		with self.assertRaises(NotAnImage):
			load_image(b"\0" * (MAX_IMAGE_BYTES + 1))
		png = _png_bytes(4, 4)
		self.assertEqual(load_image(png).mode, "RGB")
		self.assertEqual(image_format(png), "png")

	def test_truncated_png_is_not_an_image(self):
		png = _png_bytes(64, 64)
		with self.assertRaises(NotAnImage):
			load_image(png[:len(png) // 2])

	def test_mpo_is_accepted_as_jpg(self):
		buf = io.BytesIO()
		Image.new("RGB", (8, 8), "red").save(buf, "MPO")
		data = buf.getvalue()
		self.assertEqual(image_format(data), "jpg")
		self.assertEqual(load_image(data).mode, "RGB")

	def test_upsert_survives_concurrent_collection_create(self):
		client = image_search.get_qdrant_client()
		real = client.collection_exists
		calls = []

		def racy(name):
			if not calls:
				calls.append(1)
				client.create_collection(name, vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE))
				return False
			return real(name)

		with mock.patch.object(client, "collection_exists", side_effect=racy):
			upsert_image("c_images", self.A1, [1, 0, 0, 0], "pA")
		self.assertEqual(search_products("c_images", [1, 0, 0, 0])[0][0], "pA")
