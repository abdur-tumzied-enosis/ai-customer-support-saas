import base64
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase

from core.storage import object_size, presign_put, r2_storages, read_head, sniff_image, thumb_key

ENV = {"R2_ACCOUNT_ID": "acc", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "local"}


class R2StoragesTest(SimpleTestCase):
	def test_testing_uses_in_memory(self):
		assert r2_storages({}, debug=True, testing=True)["default"]["BACKEND"] == "django.core.files.storage.InMemoryStorage"

	def test_missing_var_raises(self):
		with self.assertRaises(ImproperlyConfigured):
			r2_storages({**ENV, "R2_BUCKET": ""}, debug=True, testing=False)

	def test_main_bucket_refused_in_debug(self):
		with self.assertRaises(ImproperlyConfigured):
			r2_storages({**ENV, "R2_BUCKET": "main"}, debug=True, testing=False)

	def test_main_bucket_allowed_in_prod(self):
		assert r2_storages({**ENV, "R2_BUCKET": "main"}, debug=False, testing=False)["default"]["OPTIONS"]["bucket_name"] == "main"

	def test_r2_options(self):
		opts = r2_storages(ENV, debug=False, testing=False)["default"]["OPTIONS"]
		assert opts["bucket_name"] == "local"
		assert opts["endpoint_url"] == "https://acc.r2.cloudflarestorage.com"
		assert opts["region_name"] == "auto" and opts["default_acl"] is None
		assert opts["querystring_auth"] is True and opts["file_overwrite"] is False


class HelpersTest(SimpleTestCase):
	def test_sniff_image(self):
		assert sniff_image(b"\xff\xd8\xff\xe0" + b"0" * 28) == "jpg"
		assert sniff_image(b"\x89PNG\r\n\x1a\n" + b"0" * 24) == "png"
		assert sniff_image(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 20) == "webp"
		assert sniff_image(b"%PDF-1.7") is None
		assert sniff_image(b"") is None

	def test_thumb_key(self):
		assert thumb_key("kb/1/products/abc.webp") == "kb/1/products/abc_thumb.webp"

	def test_presign_put_signs_checksum(self):
		client = mock.MagicMock()
		client.generate_presigned_url.return_value = "https://signed"
		with mock.patch("core.storage._client", return_value=client), mock.patch("core.storage._bucket", return_value="local"):
			r = presign_put("tmp/x.jpg", "image/jpeg", sha256_hex="00" * 32)
		kwargs = client.generate_presigned_url.call_args.kwargs
		params = kwargs["Params"]
		assert params["Bucket"] == "local" and params["Key"] == "tmp/x.jpg" and params["ContentType"] == "image/jpeg"
		assert params["ChecksumSHA256"] == base64.b64encode(bytes(32)).decode()
		assert kwargs["ExpiresIn"] == 600
		assert r == {"url": "https://signed", "headers": {"Content-Type": "image/jpeg", "x-amz-checksum-sha256": params["ChecksumSHA256"]}}

	def test_presign_put_without_checksum(self):
		client = mock.MagicMock()
		client.generate_presigned_url.return_value = "https://signed"
		with mock.patch("core.storage._client", return_value=client), mock.patch("core.storage._bucket", return_value="local"):
			r = presign_put("tmp/x.jpg", "image/jpeg")
		assert "ChecksumSHA256" not in client.generate_presigned_url.call_args.kwargs["Params"]
		assert r["headers"] == {"Content-Type": "image/jpeg"}

	def test_object_size_and_read_head_in_memory(self):
		name = default_storage.save("t/a.bin", ContentFile(b"abcdef"))
		try:
			assert object_size(name) == 6 and object_size("t/missing") is None
			assert read_head(name, 3) == b"abc"
		finally:
			default_storage.delete(name)
