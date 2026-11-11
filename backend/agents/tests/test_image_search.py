import io
import uuid
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from langchain_core.embeddings import Embeddings
from django.test import SimpleTestCase, override_settings
from PIL import Image
from qdrant_client import QdrantClient, models

from agents import image_search
from agents.image_search import (
	MAX_IMAGE_BYTES, NotAnImage, delete_images, image_format, load_image, make_variants, search_products, upsert_image,
)


class _Probe(Embeddings):
	def __init__(self):
		self.usage = []

	def embed_documents(self, texts):
		return [[1.0, 0, 0, 0] for _ in texts]

	def embed_query(self, text):
		self.usage.append(object())
		return [1.0, 0, 0, 0]


def _png_bytes(w, h):
	buf = io.BytesIO()
	Image.new("RGB", (w, h), "red").save(buf, "PNG")
	return buf.getvalue()


class ImageSearchTest(SimpleTestCase):
	"""In-memory Qdrant with hand-made 4-dim vectors; no model is called."""

	def setUp(self):
		client = QdrantClient(":memory:")
		for p in (
			mock.patch.object(image_search, "get_qdrant_client", return_value=client),
			mock.patch.object(image_search, "get_default_embeddings", side_effect=_Probe),
		):
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

	def test_exif_rotation_is_applied(self):
		img = Image.new("RGB", (40, 20), "red")
		exif = img.getexif()
		exif[0x0112] = 6  # Orientation: rotate 90 CW to display
		buf = io.BytesIO()
		img.save(buf, "JPEG", exif=exif)
		self.assertEqual(load_image(buf.getvalue()).size, (20, 40))

	def test_transparency_becomes_white(self):
		for img in (Image.new("RGBA", (4, 4), (0, 0, 0, 0)), Image.new("LA", (4, 4), (0, 0))):
			buf = io.BytesIO()
			img.save(buf, "PNG")
			out = load_image(buf.getvalue())
			self.assertEqual(out.mode, "RGB")
			self.assertEqual(out.getpixel((0, 0)), (255, 255, 255), img.mode)

	def test_palette_transparency_becomes_white(self):
		img = Image.new("P", (4, 4), 0)
		img.putpalette([0, 0, 0] * 256)
		buf = io.BytesIO()
		img.save(buf, "PNG", transparency=0)
		self.assertEqual(load_image(buf.getvalue()).getpixel((0, 0)), (255, 255, 255))

	def test_soft_time_limit_is_not_swallowed(self):
		with mock.patch.object(image_search.Image, "open", side_effect=SoftTimeLimitExceeded()):
			with self.assertRaises(SoftTimeLimitExceeded):
				load_image(_png_bytes(4, 4))

	def test_upsert_reports_the_probe_only_when_it_creates_the_collection(self):
		usage = []
		upsert_image("c_images", self.A1, [1, 0, 0, 0], "pA", usage=usage)
		upsert_image("c_images", self.A2, [1, 0, 0, 0], "pA", usage=usage)
		self.assertEqual(len(usage), 1)
		self.assertFalse(hasattr(image_search, "embed_image"))  # photo embedding lives in embeddings_api

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


class MakeVariantsTest(SimpleTestCase):
	def test_variants_bounded_webp(self):
		display, thumb = make_variants(_png_bytes(4000, 3000))
		d, t = Image.open(io.BytesIO(display)), Image.open(io.BytesIO(thumb))
		self.assertEqual((d.format, d.size), ("WEBP", (1600, 1200)))
		self.assertEqual((t.format, t.size), ("WEBP", (320, 240)))

	def test_small_image_not_upscaled(self):
		display, thumb = make_variants(_png_bytes(200, 100))
		self.assertEqual(Image.open(io.BytesIO(display)).size, (200, 100))
		self.assertEqual(Image.open(io.BytesIO(thumb)).size, (200, 100))

	def test_exif_orientation_applied(self):
		img = Image.new("RGB", (300, 100), "red")
		exif = img.getexif()
		exif[0x0112] = 6
		buf = io.BytesIO()
		img.save(buf, "JPEG", exif=exif)
		d = Image.open(io.BytesIO(make_variants(buf.getvalue())[0]))
		self.assertEqual(d.size, (100, 300))
		self.assertFalse(d.getexif())

	def test_variants_reject_non_image(self):
		with self.assertRaises(NotAnImage):
			make_variants(b"%PDF-1.7 not an image")

	def test_variants_respect_max_bytes(self):
		with self.assertRaises(NotAnImage):
			make_variants(_png_bytes(10, 10), max_bytes=10)
