import io
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

from channels_app.media import MediaError, fetch_photo, has_storable_media
from channels_app.media_store import store_message_media
from channels_app.models import Message
from channels_app.service.whatsapp_messaging import handle_whatsapp_message
from channels_app.tests.test_whatsapp import WhatsAppBase, payload, text
from core.storage import AUDIO_MAX_BYTES, thumb_key
from core.tests.utils import fresh_storage


def png(w=2400, h=1200):
	buf = io.BytesIO()
	Image.new("RGB", (w, h), "red").save(buf, "PNG")
	return buf.getvalue()


def wa_image(wamid="wamid.img"):
	return {"from": "8801700000000", "id": wamid, "timestamp": "1700000000", "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg"}}


def wa_audio(wamid="wamid.voice", mime="audio/ogg; codecs=opus"):
	return {"from": "8801700000000", "id": wamid, "timestamp": "1700000000", "type": "audio", "audio": {"id": "media-2", "mime_type": mime}}


@fresh_storage
class StoreMessageMediaTest(WhatsAppBase):
	def setUp(self):
		super().setUp()
		self.store_delay = mock.patch("channels_app.service.whatsapp_messaging.store_message_media.delay").start()
		self.schedule = mock.patch("channels_app.media_store.schedule_reply").start()
		self.fetch = mock.patch("channels_app.media_store.fetch_attachment", return_value=png()).start()
		self.addCleanup(mock.patch.stopall)

	def inbound(self, message):
		handle_whatsapp_message(payload(message))
		return Message.objects.get(external_message_id=message["id"])

	def test_photo_stored_as_variants_and_reply_scheduled(self):
		msg = self.inbound(wa_image())
		store_message_media(str(msg.id), True)
		msg.refresh_from_db()
		path = msg.attachment_info[0]["path"]
		assert path.startswith("chat_media/p-wa/") and path.endswith(".webp"), path
		with default_storage.open(path) as f:
			assert Image.open(f).size == (1600, 800)
		assert default_storage.exists(thumb_key(path))
		self.fetch.assert_called_once()
		assert self.fetch.call_args.args[2] == 5 * 1024 * 1024
		self.schedule.assert_called_once()
		assert self.schedule.call_args.args[0].id == msg.id

	def test_audio_stored_raw(self):
		self.fetch.return_value = b"OggS-voice"
		msg = self.inbound(wa_audio())
		store_message_media(str(msg.id), False)
		msg.refresh_from_db()
		path = msg.attachment_info[0]["path"]
		assert path.startswith("chat_media/p-wa/") and path.endswith(".ogg"), path
		with default_storage.open(path) as f:
			assert f.read() == b"OggS-voice"
		assert self.fetch.call_args.args[2] == AUDIO_MAX_BYTES

	def test_audio_extension_from_mime(self):
		for mime, ext in (("audio/mpeg", ".mp3"), ("audio/mp4", ".m4a"), ("audio/amr", ".amr"), ("audio/x-weird", ".bin")):
			with self.subTest(mime=mime):
				msg = self.inbound(wa_audio(f"wamid.{ext}", mime))
				store_message_media(str(msg.id), False)
				msg.refresh_from_db()
				assert msg.attachment_info[0]["path"].endswith(ext)

	def test_oversize_audio_skipped(self):
		self.fetch.side_effect = MediaError("too large")
		msg = self.inbound(wa_audio())
		store_message_media(str(msg.id), False)
		msg.refresh_from_db()
		assert "path" not in msg.attachment_info[0]

	def test_media_failure_still_schedules_reply(self):
		self.fetch.side_effect = MediaError("download failed")
		msg = self.inbound(wa_image())
		store_message_media(str(msg.id), True)
		self.schedule.assert_called_once()

	def test_unexpected_error_still_schedules_reply(self):
		self.fetch.side_effect = RuntimeError("boom")
		msg = self.inbound(wa_image())
		store_message_media(str(msg.id), True)
		self.schedule.assert_called_once()

	def test_no_reply_flag_does_not_schedule(self):
		msg = self.inbound(wa_image())
		store_message_media(str(msg.id), False)
		self.schedule.assert_not_called()

	def test_tmp_upload_moved_to_chat_media(self):
		msg = self.inbound(wa_image())
		tmp = default_storage.save("tmp/widget/w/v/x.png", ContentFile(png(100, 50)))
		Message.objects.filter(id=msg.id).update(attachment_info=[{"type": "image", "path": tmp}])
		store_message_media(str(msg.id), True)
		msg.refresh_from_db()
		path = msg.attachment_info[0]["path"]
		assert path.startswith("chat_media/p-wa/") and not default_storage.exists(tmp)
		self.fetch.assert_not_called()

	def test_non_image_tmp_upload_is_deleted(self):
		msg = self.inbound(wa_image())
		tmp = default_storage.save("tmp/widget/w/v/x.png", ContentFile(b"%PDF not an image"))
		Message.objects.filter(id=msg.id).update(attachment_info=[{"type": "image", "path": tmp}])
		store_message_media(str(msg.id), True)
		msg.refresh_from_db()
		assert msg.attachment_info[0]["path"] == tmp  # left as is: fetch_photo will refuse it
		assert not default_storage.exists(tmp)
		self.schedule.assert_called_once()

	def test_already_stored_is_skipped(self):
		msg = self.inbound(wa_image())
		Message.objects.filter(id=msg.id).update(attachment_info=[{"type": "image", "path": "chat_media/p-wa/done.webp"}])
		store_message_media(str(msg.id), False)
		self.fetch.assert_not_called()

	def test_sticker_ignored(self):
		msg = self.inbound(wa_image())
		Message.objects.filter(id=msg.id).update(attachment_info=[{"type": "image", "payload": {"sticker_id": 1, "url": "x"}}])
		store_message_media(str(msg.id), False)
		self.fetch.assert_not_called()

	def test_deleted_message_is_noop(self):
		store_message_media("00000000-0000-0000-0000-000000000000", True)
		self.schedule.assert_not_called()

	def test_fetch_photo_prefers_stored_path(self):
		msg = self.inbound(wa_image())
		path = default_storage.save("chat_media/p-wa/a.webp", ContentFile(b"stored"))
		with mock.patch("channels_app.media._get") as get:
			assert fetch_photo(msg, {"type": "image", "path": path, "payload": {"id": "media-1"}}) == b"stored"
		get.assert_not_called()


class InboundDispatchTest(WhatsAppBase):
	def setUp(self):
		super().setUp()
		self.store_delay = mock.patch("channels_app.service.whatsapp_messaging.store_message_media.delay").start()
		self.schedule = mock.patch("channels_app.service.whatsapp_messaging.schedule_reply").start()
		self.addCleanup(mock.patch.stopall)

	def test_whatsapp_audio_message_queues_store(self):
		handle_whatsapp_message(payload(wa_audio()))
		msg = Message.objects.get(external_message_id="wamid.voice")
		self.store_delay.assert_called_once_with(str(msg.id), False)
		self.schedule.assert_not_called()

	def test_text_message_schedules_reply_directly(self):
		handle_whatsapp_message(payload(text("wamid.1")))
		self.store_delay.assert_not_called()
		self.schedule.assert_called_once()

	def test_has_storable_media(self):
		assert has_storable_media([{"type": "audio", "payload": {}}])
		assert has_storable_media([{"type": "image", "payload": {"url": "x"}}])
		assert not has_storable_media([{"type": "image", "payload": {"sticker_id": 1}}])
		assert not has_storable_media([{"type": "location", "payload": {}}])
		assert not has_storable_media([])
