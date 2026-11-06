import socket
from io import BytesIO
from unittest import mock

import requests
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from accounts.models import Project
from core.storage import thumb_key
from core.tests.utils import fresh_storage
from knowledge.image_fetch import ImageFetchError, attach_product_images, fetch_image
from knowledge.models import MAX_IMAGES_PER_PRODUCT, MAX_PRODUCT_IMAGE_BYTES, KnowledgeBase, Product, ProductImage
from knowledge.tasks import fetch_product_images


def _addr(ip):
	return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _png():
	buf = BytesIO()
	Image.new("RGB", (8, 8), "red").save(buf, "PNG")
	return buf.getvalue()


def _stream_resp(total, chunk=64 * 1024, headers=None):
	"""Response whose body is a generator of `total` bytes; state records how many chunks were pulled."""
	state = {"chunks": 0, "exhausted": False}

	def gen(chunk_size=chunk):
		sent = 0
		while sent < total:
			n = min(chunk_size, total - sent)
			sent += n
			state["chunks"] += 1
			yield b"x" * n
		state["exhausted"] = True

	resp = mock.MagicMock(status_code=200)
	resp.headers = {"Content-Type": "image/jpeg", **(headers or {})}
	resp.iter_content.side_effect = lambda chunk_size=chunk: gen(chunk_size)
	return resp, state


def _resp(status=200, body=None, ctype="image/jpeg", headers=None):
	body = _png() if body is None else body
	resp = mock.MagicMock(status_code=status)
	resp.headers = {"Content-Type": ctype, **(headers or {})} if ctype else dict(headers or {})
	resp.iter_content.side_effect = lambda chunk_size=8192: iter([body[i:i + chunk_size] for i in range(0, len(body), chunk_size)])
	return resp


@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=_addr("93.184.216.34"))
@mock.patch("knowledge.image_fetch.requests.get")
class FetchImageTest(TestCase):
	def test_fetch_ok(self, get, _dns):
		get.return_value = _resp(body=b"abc", ctype="image/png")
		self.assertEqual(fetch_image("https://shop.com/a.png"), (b"abc", "png"))
		kwargs = get.call_args.kwargs
		self.assertIs(kwargs["allow_redirects"], False)
		self.assertEqual(kwargs["timeout"], 10)
		self.assertTrue(kwargs["stream"])

	def test_fetch_blocks_internal_url(self, get, dns):
		dns.side_effect = lambda host, *a, **k: _addr(host)
		for url in ("http://127.0.0.1/a.jpg", "http://169.254.169.254/", "ftp://shop.com/a.jpg"):
			with self.assertRaises(ImageFetchError):
				fetch_image(url)
		get.assert_not_called()

	def test_redirect_not_followed(self, get, _dns):
		get.return_value = _resp(status=302, headers={"Location": "http://127.0.0.1/"})
		with self.assertRaises(ImageFetchError):
			fetch_image("https://shop.com/a.jpg")
		self.assertIs(get.call_args.kwargs["allow_redirects"], False)

	def test_oversize_cut_off_while_streaming(self, get, _dns):
		resp, state = _stream_resp(MAX_PRODUCT_IMAGE_BYTES * 3)
		get.return_value = resp
		with self.assertRaises(ImageFetchError):
			fetch_image("https://shop.com/a.jpg")
		resp.close.assert_called()
		self.assertFalse(state["exhausted"])
		self.assertEqual(state["chunks"], MAX_PRODUCT_IMAGE_BYTES // (64 * 1024) + 1)

	def test_content_length_lie_still_capped(self, get, _dns):
		resp, state = _stream_resp(11 * 1024 * 1024 * 3, headers={"Content-Length": "1024"})
		get.return_value = resp
		with self.assertRaises(ImageFetchError) as cm:
			fetch_image("https://shop.com/a.jpg")
		self.assertEqual(str(cm.exception), "too_large")
		self.assertFalse(state["exhausted"])
		self.assertLessEqual(state["chunks"], MAX_PRODUCT_IMAGE_BYTES // (64 * 1024) + 1)

	@mock.patch("knowledge.image_fetch.monotonic", side_effect=[0, 1, 1000, 1000, 1000])
	def test_slow_drip_hits_deadline(self, _mono, get, _dns):
		resp, state = _stream_resp(MAX_PRODUCT_IMAGE_BYTES, chunk=1024)
		get.return_value = resp
		with self.assertRaises(ImageFetchError) as cm:
			fetch_image("https://shop.com/a.jpg")
		self.assertEqual(str(cm.exception), "too_slow")
		self.assertFalse(state["exhausted"])
		self.assertEqual(state["chunks"], 2)
		resp.close.assert_called()

	@mock.patch("knowledge.image_fetch.monotonic", side_effect=[0, 1, 1000, 1000, 1000])
	def test_slow_drip_marks_row_failed(self, _mono, get, _dns):
		resp, state = _stream_resp(MAX_PRODUCT_IMAGE_BYTES, chunk=1024)
		get.return_value = resp
		project = Project.objects.create(name="P2", slug="0123456789abcdef0123456789abcf2")
		kb = KnowledgeBase.objects.create(project=project, name="default", vector_collection="vc-a10-slow")
		product = Product.objects.create(kb=kb, name="Shoe")
		attach_product_images(product, ["https://shop.com/a.jpg"])
		img = product.images.get()
		self.assertEqual((img.status, img.error), ("failed", "too_slow"))
		self.assertFalse(state["exhausted"])

	def test_oversize_declared_length_rejected(self, get, _dns):
		get.return_value = _resp(headers={"Content-Length": str(MAX_PRODUCT_IMAGE_BYTES + 1)})
		with self.assertRaises(ImageFetchError):
			fetch_image("https://shop.com/a.jpg")

	def test_bad_type(self, get, _dns):
		for ctype in ("text/html", "image/svg+xml", None):
			get.return_value = _resp(ctype=ctype)
			with self.assertRaises(ImageFetchError):
				fetch_image("https://shop.com/a.jpg")

	def test_network_error(self, get, _dns):
		get.side_effect = requests.ConnectionError("boom http://x/?token=secret")
		with self.assertRaises(ImageFetchError) as cm:
			fetch_image("https://shop.com/a.jpg")
		self.assertNotIn("secret", str(cm.exception))


@fresh_storage
@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=_addr("93.184.216.34"))
@mock.patch("knowledge.image_fetch.requests.get")
class AttachTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abcf")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-a10")
		self.product = Product.objects.create(kb=self.kb, name="Shoe")
		p = mock.patch("knowledge.image_fetch.embed_product_image").start()
		self.embed = p.delay
		self.addCleanup(mock.patch.stopall)

	def _on(self, value):
		return mock.patch.object(Project, "image_search_on", new_callable=mock.PropertyMock, return_value=value)

	def test_stores_ready(self, get, _dns):
		get.return_value = _resp(body=_png(), ctype="image/png")
		with self._on(False):
			attach_product_images(self.product, ["https://shop.com/a.png"])
		img = self.product.images.get()
		self.assertEqual((img.status, img.error), ("ready", ""))
		self.assertTrue(img.file.name.startswith(f"products/{self.project.slug}/{self.kb.id.hex}/{img.id}."))
		self.assertTrue(img.file.name.endswith(".webp"))
		with default_storage.open(img.file.name) as f:
			self.assertEqual(Image.open(f).format, "WEBP")
		self.assertTrue(default_storage.exists(thumb_key(img.file.name)))

	def test_non_image_body_marked_failed(self, get, _dns):
		get.return_value = _resp(body=b"<html>not an image</html>", ctype="image/png")
		with self._on(False):
			attach_product_images(self.product, ["https://shop.com/a.png"])
		img = self.product.images.get()
		self.assertEqual((img.status, img.error), ("failed", "not_an_image"))
		self.assertFalse(img.file)
		self.assertEqual(self._stored_for(img), [])

	def test_thumb_save_failure_leaves_no_orphan(self, get, _dns):
		get.return_value = _resp(ctype="image/png")
		real_save = default_storage.save

		def flaky(name, content, *a, **k):
			if name.endswith("_thumb.webp"):
				raise OSError("disk full")
			return real_save(name, content, *a, **k)

		with self._on(False), mock.patch.object(default_storage, "save", side_effect=flaky):
			attach_product_images(self.product, ["https://shop.com/a.png"])
		img = self.product.images.get()
		self.assertEqual(img.status, "failed")
		self.assertEqual(self._stored_for(img), [])

	def _stored_for(self, img):
		try:
			_dirs, files = default_storage.listdir(f"products/{self.project.slug}/{self.kb.id.hex}/")
		except (FileNotFoundError, OSError):
			return []
		return [f for f in files if str(img.id) in f]

	def test_product_kept_when_image_fails(self, get, _dns):
		get.return_value = _resp(ctype="text/html")
		with self._on(True), self.captureOnCommitCallbacks(execute=True):
			attach_product_images(self.product, ["https://shop.com/a.jpg?token=abc"])
		self.assertTrue(Product.objects.filter(id=self.product.id).exists())
		img = self.product.images.get()
		self.assertEqual(img.status, "failed")
		self.assertTrue(img.error)
		self.assertNotIn("token", img.error)
		self.embed.assert_not_called()

	def test_one_failure_does_not_stop_others(self, get, _dns):
		get.side_effect = [_resp(ctype="text/html"), _resp()]
		with self._on(False):
			attach_product_images(self.product, ["https://shop.com/a.jpg", "https://shop.com/b.jpg"])
		self.assertEqual(sorted(self.product.images.values_list("status", flat=True)), ["failed", "ready"])

	def test_price_and_status_untouched(self, get, _dns):
		get.return_value = _resp()
		with self._on(False):
			attach_product_images(self.product, ["https://shop.com/a.jpg"])
		self.product.refresh_from_db()
		self.assertEqual((self.product.status, self.product.price), ("needs_info", None))

	def test_embed_only_when_image_search_on(self, get, _dns):
		get.return_value = _resp()
		with self._on(False), self.captureOnCommitCallbacks(execute=True):
			attach_product_images(self.product, ["https://shop.com/a.jpg"])
		self.embed.assert_not_called()
		with self._on(True), self.captureOnCommitCallbacks(execute=True):
			attach_product_images(self.product, ["https://shop.com/b.jpg"])
		ready = self.product.images.filter(status="ready").order_by("-created_at").first()
		self.embed.assert_called_once_with(str(ready.id))

	def test_cap_per_product_and_dedupe(self, get, _dns):
		get.return_value = _resp()
		urls = [f"https://shop.com/{i}.jpg" for i in range(15)] + ["https://shop.com/0.jpg"]
		with self._on(False):
			attach_product_images(self.product, urls)
		self.assertEqual(self.product.images.count(), MAX_IMAGES_PER_PRODUCT)
		self.assertEqual(get.call_count, MAX_IMAGES_PER_PRODUCT)

	def test_task_runs_download(self, get, _dns):
		get.return_value = _resp()
		with self._on(False):
			fetch_product_images(str(self.product.id), ["https://shop.com/a.jpg"])
		self.assertEqual(ProductImage.objects.filter(product=self.product, status="ready").count(), 1)

	def test_task_missing_product_noop(self, get, _dns):
		fetch_product_images("00000000-0000-0000-0000-000000000000", ["https://shop.com/a.jpg"])
		get.assert_not_called()
