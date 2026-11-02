from types import SimpleNamespace
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase

from agents.image_search import MAX_IMAGE_BYTES
from channels_app.media import MediaError, fetch_photo, is_photo
from PIL import Image
import io


def png_bytes():
	buf = io.BytesIO()
	Image.new("RGB", (4, 4), "red").save(buf, "PNG")
	return buf.getvalue()


def msg(channel):
	return SimpleNamespace(conversation=SimpleNamespace(channel=channel))


def response(body=b"", json=None, status=200, chunk=1024):
	resp = mock.Mock()
	resp.status_code = status
	resp.raise_for_status.side_effect = None if status < 400 else Exception("http error")
	resp.json.return_value = json
	resp.iter_content.side_effect = lambda chunk_size=None: iter([body[i:i + chunk] for i in range(0, len(body), chunk)])
	return resp


class IsPhotoTest(SimpleTestCase):
	def test_sticker_is_not_a_photo(self):
		self.assertFalse(is_photo({"type": "image", "payload": {"sticker_id": 369239263222822, "url": "https://scontent.xx.fbcdn.net/x"}}))
		self.assertTrue(is_photo({"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}}))
		self.assertFalse(is_photo({"type": "audio"}))


@mock.patch("channels_app.media.requests.get")
class FetchRemoteTest(SimpleTestCase):
	def test_facebook_fetches_meta_cdn(self, get):
		get.return_value = response(b"abc")
		self.assertEqual(fetch_photo(msg("facebook"), {"type": "image", "url": "https://scontent.xx.fbcdn.net/x"}), b"abc")
		kwargs = get.call_args.kwargs
		self.assertTrue(kwargs["stream"])
		self.assertEqual(kwargs["timeout"], 10)
		self.assertFalse(kwargs["allow_redirects"])

	def test_facebook_rejects_non_meta_host(self, get):
		for url in (
			"http://169.254.169.254/",
			"https://evilfbcdn.net/x",
			"https://scontent.fbcdn.net@evil.com/x",
			"https://evil.com/?a=.fbcdn.net",
			"http://scontent.xx.fbcdn.net/x",
			"https://fbcdn.net.evil.com/x",
		):
			with self.assertRaises(MediaError, msg=url):
				fetch_photo(msg("instagram"), {"type": "image", "url": url})
		get.assert_not_called()

	def test_redirect_is_an_error(self, get):
		get.return_value = response(b"", status=302)
		with self.assertRaises(MediaError):
			fetch_photo(msg("facebook"), {"type": "image", "url": "https://x.fbcdn.net/x"})

	def test_whatsapp_two_step_fetch_uses_token(self, get):
		get.side_effect = [response(json={"url": "https://lookaside.fbsbx.com/m"}), response(b"imgbytes")]
		creds = SimpleNamespace(access_token="tok")
		cred = mock.Mock()
		cred.get_credentials.return_value = creds
		with mock.patch("channels_app.media.tasks.get_whatsapp_credential", return_value=cred):
			out = fetch_photo(msg("whatsapp"), {"type": "image", "payload": {"id": "MID1"}})
		self.assertEqual(out, b"imgbytes")
		self.assertTrue(get.call_args_list[0].args[0].endswith("/MID1"))
		for call in get.call_args_list:
			self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer tok"})

	def test_whatsapp_rejects_non_meta_media_url(self, get):
		get.side_effect = [response(json={"url": "http://169.254.169.254/"})]
		cred = mock.Mock()
		cred.get_credentials.return_value = SimpleNamespace(access_token="tok")
		with mock.patch("channels_app.media.tasks.get_whatsapp_credential", return_value=cred):
			with self.assertRaises(MediaError):
				fetch_photo(msg("whatsapp"), {"type": "image", "payload": {"id": "MID1"}})
		self.assertEqual(get.call_count, 1)

	def test_whatsapp_without_credential_raises(self, get):
		with mock.patch("channels_app.media.tasks.get_whatsapp_credential", return_value=None):
			with self.assertRaises(MediaError):
				fetch_photo(msg("whatsapp"), {"type": "image", "payload": {"id": "MID1"}})
		get.assert_not_called()

	def test_oversize_download_raises(self, get):
		get.return_value = response(b"x" * (MAX_IMAGE_BYTES + 1))
		with self.assertRaises(MediaError):
			fetch_photo(msg("facebook"), {"type": "image", "url": "https://x.fbcdn.net/x"})

	def test_network_error_is_media_error(self, get):
		import requests
		get.side_effect = requests.ConnectionError("boom")
		with self.assertRaises(MediaError):
			fetch_photo(msg("facebook"), {"type": "image", "url": "https://x.fbcdn.net/x"})

	def test_soft_time_limit_is_not_swallowed(self, get):
		url = {"type": "image", "url": "https://x.fbcdn.net/x"}
		get.side_effect = SoftTimeLimitExceeded()
		with self.assertRaises(SoftTimeLimitExceeded):
			fetch_photo(msg("facebook"), url)
		get.side_effect = None
		slow = response(b"abc")
		slow.iter_content.side_effect = SoftTimeLimitExceeded()
		get.return_value = slow
		with self.assertRaises(SoftTimeLimitExceeded):
			fetch_photo(msg("facebook"), url)
		lookup = response(json={"url": "https://lookaside.fbsbx.com/m"})
		lookup.json.side_effect = SoftTimeLimitExceeded()
		get.return_value = lookup
		cred = mock.Mock()
		cred.get_credentials.return_value = SimpleNamespace(access_token="tok")
		with mock.patch("channels_app.media.tasks.get_whatsapp_credential", return_value=cred):
			with self.assertRaises(SoftTimeLimitExceeded):
				fetch_photo(msg("whatsapp"), {"type": "image", "payload": {"id": "MID1"}})

	def test_unsupported_channel(self, get):
		with self.assertRaises(MediaError):
			fetch_photo(msg("telegram"), {"type": "image"})


class StoredPathTest(SimpleTestCase):
	def test_stored_photo_round_trip(self):
		from django.core.files.base import ContentFile
		import channels_app.media as media
		data = png_bytes()
		path = media.default_storage.save("chat_media/p/x.webp", ContentFile(data))
		self.assertEqual(fetch_photo(msg("self"), {"type": "image", "path": path}), data)
		self.assertEqual(fetch_photo(msg("widget"), {"type": "image", "path": path}), data)

	def test_path_traversal_and_foreign_prefix_rejected(self):
		for path in ("../secret.png", "chat_media/../secret.png", "/etc/passwd", "other/x.png", "tmp/widget/w/v/x.png",
				"widget_uploads/a.png", "chat_media/a/../../x", ""):
			with self.assertRaises(MediaError, msg=path):
				fetch_photo(msg("widget"), {"type": "image", "path": path})

	def test_missing_file_is_media_error(self):
		with self.assertRaises(MediaError):
			fetch_photo(msg("self"), {"type": "image", "path": "chat_media/none.webp"})

	def test_soft_time_limit_reading_upload_is_not_swallowed(self):
		import channels_app.media as media
		with mock.patch.object(media.default_storage, "open", side_effect=SoftTimeLimitExceeded()):
			with self.assertRaises(SoftTimeLimitExceeded):
				fetch_photo(msg("self"), {"type": "image", "path": "chat_media/x.webp"})

	def test_oversize_stored_file_raises(self):
		from django.core.files.base import ContentFile
		import channels_app.media as media
		p = media.default_storage.save("chat_media/big.bin", ContentFile(b"x" * (MAX_IMAGE_BYTES + 1)))
		with self.assertRaises(MediaError):
			fetch_photo(msg("self"), {"type": "image", "path": p})
